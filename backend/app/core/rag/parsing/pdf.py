"""PDF parser — extracts structured markdown from PDFs using pdfplumber.

Handles bordered tables, borderless tables, cross-page table merging,
and text block classification (heading / key-value / footnote / body).
"""

from __future__ import annotations

import asyncio
import io
import re
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import pdfplumber
from pdfplumber.page import Page

from app.core.config import get_settings
from app.core.utils import singleton
from app.core.logger import setup_logger

logger = setup_logger(__name__)


@singleton
def _get_pdf_pool() -> ProcessPoolExecutor:
    """Lazily create a shared process pool for CPU-bound PDF parsing."""
    settings = get_settings()
    return ProcessPoolExecutor(max_workers=settings.PDF_PARSE_WORKERS)


# ---------------------------------------------------------------------------
# Table extraction strategies
# ---------------------------------------------------------------------------

_BORDERED: dict[str, Any] = dict(
    vertical_strategy="lines", horizontal_strategy="lines",
    snap_x_tolerance=4, snap_y_tolerance=4,
    join_x_tolerance=4, join_y_tolerance=4,
    intersection_x_tolerance=8, intersection_y_tolerance=8,
    text_x_tolerance=3, text_y_tolerance=3,
)

_BORDERLESS: dict[str, Any] = dict(
    vertical_strategy="text", horizontal_strategy="text",
    min_words_vertical=3, min_words_horizontal=1,
    snap_x_tolerance=5, snap_y_tolerance=5,
    intersection_x_tolerance=10, intersection_y_tolerance=10,
    text_x_tolerance=3, text_y_tolerance=3,
)

_MIN_EDGES = 4
_KV_RE = re.compile(r"^(.+?)\s*:\s+(.+)$")


class PdfParser:
    """Parses PDF files into markdown using pdfplumber.

    Runs CPU-bound parsing in a process pool to avoid blocking the
    event loop and bypass the GIL for true parallelism.
    """

    async def parse(self, file_content: bytes, filename: str) -> str:
        """Parse a PDF from raw bytes into markdown.

        Raises:
            ValueError: If the PDF cannot be opened or has no content.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _get_pdf_pool(), _parse_pdf_sync, file_content, filename,
        )


# ---------------------------------------------------------------------------
# Synchronous entry point (process-pool worker)
# ---------------------------------------------------------------------------

def _parse_pdf_sync(file_content: bytes, filename: str) -> str:
    """Synchronous PDF parsing — executed in a process pool worker.

    Raises:
        ValueError: If the PDF cannot be opened or has no content.
    """
    try:
        pdf_file = io.BytesIO(file_content)
        with pdfplumber.open(pdf_file, unicode_norm="NFKC") as pdf:
            pages = pdf.pages
            tables = _extract_all_tables(pages)

            page_blocks: dict[int, list] = {}
            for p in pages:
                blocks = _extract_text_blocks(p)
                if blocks:
                    page_blocks[p.page_number] = blocks

            page_tables: dict[int, list[tuple]] = {}
            for hdr, rows, caption, pr in tables:
                page_tables.setdefault(pr[0], []).append((hdr, rows, caption, pr))

            table_y: dict[int, dict[int, float]] = {}
            for p in pages:
                settings = _BORDERED if _is_bordered(p) else _BORDERLESS
                tbls = p.find_tables(table_settings=settings)
                for i, t in enumerate(tbls):
                    if t.bbox:
                        table_y.setdefault(p.page_number, {})[i] = t.bbox[1]

        sections: list[str] = []
        for pn in sorted(set(list(page_blocks) + list(page_tables))):
            items: list[tuple[float, str]] = []

            for kind, text, y in page_blocks.get(pn, []):
                items.append((y, _md_block(kind, text)))

            for idx, (hdr, rows, caption, _) in enumerate(page_tables.get(pn, [])):
                y = table_y.get(pn, {}).get(idx, 0.0)
                items.append((y, _md_table(hdr, rows, caption)))

            items.sort(key=lambda x: x[0])
            page_md = "\n\n".join(s for _, s in items)
            if page_md.strip():
                sections.append(page_md)

        result = "\n\n".join(sections) + "\n"

        if not result.strip():
            raise ValueError(f"No text content extracted from PDF: {filename}")

        return result

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to parse PDF '{filename}': {e}") from e


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

def _is_bordered(page: Page) -> bool:
    """True when the page has enough ruling lines for bordered table detection."""
    return len(page.edges) >= _MIN_EDGES


# ---------------------------------------------------------------------------
# Cell / row helpers
# ---------------------------------------------------------------------------

def _cell(c: str | None) -> str:
    return (c or "").strip()


def _empty_row(row: list) -> bool:
    return all(not _cell(c) for c in row)


def _join_fragments(parts: list[str]) -> str:
    """Smart-join cell fragments split across merged columns."""
    non_empty = [p for p in parts if p.strip()]
    if not non_empty:
        return ""
    result = non_empty[0]
    for p in non_empty[1:]:
        if p[0].islower() or (p[0].isdigit() and result[-1:].isdigit()) or result.endswith(("-", "/")):
            result += p
        else:
            result += " " + p
    return result.strip()


# ---------------------------------------------------------------------------
# Borderless repair utilities
# ---------------------------------------------------------------------------

def _repair_right_edge(page: Page, tbl: Any, rows: list[list[str]]) -> None:
    """Append overflow characters beyond the table's right boundary."""
    if not tbl.bbox:
        return
    tbl_right = tbl.bbox[2]
    if page.width - tbl_right < 5:
        return
    overflow = [c for c in page.chars if tbl_right - 1 <= c["x0"] < page.width]
    if not overflow:
        return

    cell_rows: dict[float, list] = {}
    for cell_item in tbl.cells:
        cell_rows.setdefault(round(cell_item[1], 1), []).append(cell_item)
    tops = sorted(cell_rows)
    if len(tops) != len(rows):
        return
    centres = [(t + max(c[3] for c in cell_rows[t])) / 2 for t in tops]

    for ch in overflow:
        ch_y = (ch["top"] + ch.get("bottom", ch["top"])) / 2
        idx = min(range(len(centres)), key=lambda i: abs(centres[i] - ch_y))
        cur = rows[idx][-1] or ""
        rows[idx][-1] = cur + ch.get("text", "")


def _merge_empty_header_cols(
    headers: list[str], rows: list[list[str]],
) -> tuple[list[str], list[list[str]]]:
    """Merge columns whose header is empty into the preceding column."""
    if len(headers) < 2:
        return headers, rows
    groups: list[tuple[str, list[int]]] = []
    for i, h in enumerate(headers):
        if h.strip():
            groups.append((h, [i]))
        elif groups:
            groups[-1][1].append(i)
        else:
            groups.append((h, [i]))
    if all(len(g[1]) == 1 for g in groups):
        return headers, rows
    new_h = [h for h, _ in groups]
    new_rows = [
        [_join_fragments([row[j] if j < len(row) else "" for j in idxs]) for _, idxs in groups]
        for row in rows
    ]
    return new_h, new_rows


def _is_spillover(row: list[str]) -> bool:
    """True if row looks like a title that leaked across cells."""
    if not row:
        return False
    non_empty = [c for c in row if c.strip()]
    if 1 - len(non_empty) / len(row) < 0.4 or not non_empty:
        return False
    if any(len(c) > 1 and c.replace(".", "", 1).isdigit() for c in non_empty):
        return False
    return len(" ".join(non_empty)) > 3


def _fix_alpha_bleed(headers: list[str], rows: list[list[str]]) -> None:
    """Fix characters that bled from a text column into an adjacent numeric column."""
    numeric_hints = {"units", "sold", "stock", "price", "quantity", "total", "cost", "amount"}
    numeric_cols = {i for i, h in enumerate(headers) if set(h.lower().split()) & numeric_hints}
    if not numeric_cols:
        return
    for row in rows:
        for ci in sorted(numeric_cols):
            if ci < 1 or ci >= len(row) or not row[ci]:
                continue
            m = re.match(r"^([a-zA-Z]+)(\d.*)$", row[ci])
            if m:
                row[ci - 1] = (row[ci - 1] or "") + m.group(1)
                row[ci] = m.group(2)


def _strip_spillover(
    headers: list[str], rows: list[list[str]],
) -> tuple[list[str], list[list[str]], str | None]:
    """Remove title spillover from the top of borderless tables."""
    captions: list[str] = []
    while headers and _is_spillover(headers):
        captions.append(_join_fragments(headers))
        headers, rows = (rows[0] if rows else []), (rows[1:] if rows else [])
    while rows and _is_spillover(rows[0]):
        captions.append(_join_fragments(rows[0]))
        rows = rows[1:]
    return headers, rows, (" · ".join(captions) if captions else None)


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------

def _extract_page_tables(page: Page) -> list[tuple[Any, list[list[str]]]]:
    """Extract tables from a single page."""
    settings = _BORDERED if _is_bordered(page) else _BORDERLESS
    borderless = not _is_bordered(page)
    results: list[tuple[Any, list[list[str]]]] = []
    for tbl in page.find_tables(table_settings=settings):
        raw = tbl.extract()
        if not raw:
            continue
        if borderless and len(raw[0]) < 2:
            continue
        if borderless:
            _repair_right_edge(page, tbl, raw)
        clean = [[_cell(c) for c in r] for r in raw if not _empty_row(r)]
        if clean:
            results.append((tbl, clean))
    return results


def _col_positions(tbl: Any) -> list[float]:
    """Sorted unique x-positions of table cell left edges."""
    return sorted({round(c[0], 1) for c in tbl.cells}) if tbl.cells else []


def _should_merge(
    prev_tbl: Any, prev_rows: list[list[str]],
    cur_tbl: Any, cur_rows: list[list[str]],
) -> bool:
    """Decide if two consecutive tables should be merged (cross-page continuation)."""
    if not prev_rows or not cur_rows or len(prev_rows[0]) != len(cur_rows[0]):
        return False
    a, b = _col_positions(prev_tbl), _col_positions(cur_tbl)
    return len(a) == len(b) and all(abs(x - y) <= 15 for x, y in zip(a, b))


def _extract_all_tables(
    pages: list[Page],
) -> list[tuple[list[str], list[list[str]], str | None, tuple[int, int]]]:
    """Extract and merge tables across all pages."""
    per_page: list[tuple[int, Any, list[list[str]]]] = []
    for p in pages:
        for tbl, rows in _extract_page_tables(p):
            per_page.append((p.page_number, tbl, rows))
    if not per_page:
        return []

    merged: list[tuple[list[str], list[list[str]], str | None, tuple[int, int]]] = []
    i = 0
    while i < len(per_page):
        pn, tbl, rows = per_page[i]
        header = rows[0]
        data = list(rows[1:])
        last_pn, last_tbl = pn, tbl

        j = i + 1
        while j < len(per_page):
            npn, ntbl, nrows = per_page[j]
            if npn != last_pn + 1 or not _should_merge(last_tbl, rows, ntbl, nrows):
                break
            cont = nrows[1:] if nrows and [_cell(c) for c in nrows[0]] == header else nrows
            data.extend([_cell(c) for c in r] for r in cont)
            last_pn, last_tbl = npn, ntbl
            j += 1

        borderless = not _is_bordered(pages[0]) if pages else False
        caption = None
        if borderless:
            header, data, caption = _strip_spillover(header, data)
            header, data = _merge_empty_header_cols(header, data)
            _fix_alpha_bleed(header, data)

        ncols = len(header)
        data = [(r + [""] * ncols)[:ncols] for r in data]
        merged.append((header, data, caption, (pn, last_pn)))
        i = j
    return merged


# ---------------------------------------------------------------------------
# Text extraction (non-table regions)
# ---------------------------------------------------------------------------

def _table_bboxes(page: Page) -> list[tuple[float, float, float, float]]:
    """Get bounding boxes of real tables on the page."""
    settings = _BORDERED if _is_bordered(page) else _BORDERLESS
    borderless = not _is_bordered(page)
    bboxes: list[tuple[float, float, float, float]] = []
    for t in page.find_tables(table_settings=settings):
        if not t.bbox:
            continue
        if borderless:
            raw = t.extract()
            if raw and len(raw[0]) < 2:
                continue
        x0, top, x1, bot = t.bbox
        pad = 20 if borderless else 0
        bboxes.append((x0, top, x1 + pad, bot + pad))
    return bboxes


def _inside(ch: dict, bboxes: list[tuple[float, float, float, float]], margin: int = 6) -> bool:
    """True if the character falls inside any bounding box."""
    x, y = ch["x0"], ch["top"]
    return any(
        bx0 - margin <= x <= bx1 + margin and by0 - margin <= y <= by1 + margin
        for bx0, by0, bx1, by1 in bboxes
    )


def _extract_text_blocks(page: Page) -> list[tuple[str, str, float]]:
    """Extract non-table text as classified ``(kind, text, y)`` blocks."""
    bboxes = _table_bboxes(page)
    chars = [c for c in page.chars if not _inside(c, bboxes)]
    if not chars:
        return []

    sizes = sorted(c["size"] for c in chars if c.get("size"))
    median_size = sizes[len(sizes) // 2] if sizes else 12

    chars.sort(key=lambda c: (c["top"], c["x0"]))
    lines: list[list[dict]] = []
    cur = [chars[0]]
    for ch in chars[1:]:
        if abs(ch["top"] - cur[-1]["top"]) <= 4:
            cur.append(ch)
        else:
            lines.append(cur)
            cur = [ch]
    lines.append(cur)

    blocks: list[tuple[str, str, float]] = []
    for line in lines:
        line.sort(key=lambda c: c["x0"])
        parts: list[str] = []
        for i, ch in enumerate(line):
            if i > 0:
                gap = ch["x0"] - line[i - 1].get("x1", ch["x0"])
                if gap > ch.get("size", 10) * 0.3:
                    parts.append(" ")
            parts.append(ch.get("text", ""))
        text = "".join(parts).strip()
        if not text:
            continue

        avg_size = sum(c["size"] for c in line if c.get("size")) / len(line)
        y_pos = line[0]["top"]

        if re.match(r"^Page\s+\d+", text, re.I):
            continue
        elif avg_size >= median_size * 1.25:
            blocks.append(("heading", text, y_pos))
        elif y_pos >= page.height * 0.88 and avg_size < median_size:
            blocks.append(("footnote", text, y_pos))
        elif _KV_RE.match(text):
            blocks.append(("kv", text, y_pos))
        else:
            blocks.append(("text", text, y_pos))
    return blocks


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _md_table(
    headers: list[str], rows: list[list[str]], caption: str | None = None,
) -> str:
    """Render a markdown table from headers and rows."""
    ncols = len(headers)
    widths = [max(3, len(h)) for h in headers]
    for r in rows:
        for i, c in enumerate(r[:ncols]):
            widths[i] = max(widths[i], len(c))

    def fmt(row: list[str]) -> str:
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(row[:ncols])) + " |"

    parts: list[str] = []
    if caption:
        parts += [f"**{caption}**", ""]
    parts.append(fmt(headers))
    parts.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for r in rows:
        parts.append(fmt(r))
    return "\n".join(parts)


def _md_block(kind: str, text: str) -> str:
    """Render a classified text block as markdown."""
    if kind == "heading":
        return f"## {text}"
    if kind == "kv":
        k, _, v = text.partition(":")
        return f"**{k.strip()}:** {v.strip()}"
    if kind == "footnote":
        return f"> {text}"
    return text
