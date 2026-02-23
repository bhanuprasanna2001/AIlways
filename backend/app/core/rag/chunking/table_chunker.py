import hashlib

import tiktoken

from app.core.rag.chunking.base import ChunkData
from app.core.rag.chunking.header_builder import build_header

_ENCODER = tiktoken.get_encoding("cl100k_base")

# Row thresholds for table chunking strategy
_SMALL_TABLE_MAX_ROWS = 15
_MEDIUM_TABLE_MAX_ROWS = 50
_ROWS_PER_PAGE = 25


def _token_count(text: str) -> int:
    """Count tokens using cl100k_base encoding."""
    return len(_ENCODER.encode(text))


def _content_hash(text: str) -> str:
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_rows(table_content: str) -> list[str]:
    """Split table content into rows by newlines.

    Args:
        table_content: Raw table text (markdown or plain).

    Returns:
        list[str]: Non-empty rows.
    """
    return [r for r in table_content.strip().split("\n") if r.strip()]


def _detect_header(rows: list[str]) -> str | None:
    """Extract the table header (first row + optional separator row).

    In markdown tables the first row is column names and the second
    is the separator (e.g. |---|---|). We keep both as the header.

    Args:
        rows: All table rows.

    Returns:
        str | None: Header text or None if table is empty.
    """
    if not rows:
        return None

    header_lines = [rows[0]]
    if len(rows) > 1 and all(c in "-| " for c in rows[1].strip()):
        header_lines.append(rows[1])

    return "\n".join(header_lines)


def chunk_table(
    table_content: str,
    doc_title: str | None,
    section_heading: str | None,
    page_number: int | None,
    start_index: int,
) -> list[ChunkData]:
    """Chunk a table into appropriately-sized pieces.

    Strategy by row count:
      - ≤15 rows  → single child chunk
      - 16–50 rows → split into groups, repeat header in each
      - >50 rows  → paginate (25 rows/chunk), create summary parent

    Args:
        table_content: Raw table text.
        doc_title: Document title for contextual header.
        section_heading: Section heading for contextual header.
        page_number: Page number for contextual header.
        start_index: Starting chunk_index for this batch.

    Returns:
        list[ChunkData]: Table chunks with appropriate types.
    """
    rows = _extract_rows(table_content)
    if not rows:
        return []

    header = _detect_header(rows)
    # Data rows start after header + optional separator
    data_start = 1
    if len(rows) > 1 and all(c in "-| " for c in rows[1].strip()):
        data_start = 2
    data_rows = rows[data_start:]
    row_count = len(data_rows)

    # Small table → single child chunk
    if row_count <= _SMALL_TABLE_MAX_ROWS:
        return [_make_table_chunk(
            content=table_content.strip(),
            doc_title=doc_title,
            section_heading=section_heading,
            page_number=page_number,
            chunk_index=start_index,
            chunk_type="child",
        )]

    chunks: list[ChunkData] = []
    parent_index: int | None = None

    # Large table (>50 rows) → create summary parent first
    if row_count > _MEDIUM_TABLE_MAX_ROWS:
        summary = f"Table with {row_count} data rows."
        if header:
            summary = f"{header}\n\n{summary}"
        parent_chunk = _make_table_chunk(
            content=summary,
            doc_title=doc_title,
            section_heading=section_heading,
            page_number=page_number,
            chunk_index=start_index,
            chunk_type="parent",
        )
        chunks.append(parent_chunk)
        parent_index = start_index

    # Split data rows into pages
    for page_start in range(0, len(data_rows), _ROWS_PER_PAGE):
        page_rows = data_rows[page_start : page_start + _ROWS_PER_PAGE]
        parts = []
        if header:
            parts.append(header)
        parts.extend(page_rows)
        content = "\n".join(parts)

        child = _make_table_chunk(
            content=content,
            doc_title=doc_title,
            section_heading=section_heading,
            page_number=page_number,
            chunk_index=start_index + len(chunks),
            chunk_type="child",
            parent_index=parent_index,
        )
        chunks.append(child)

    return chunks


def _make_table_chunk(
    content: str,
    doc_title: str | None,
    section_heading: str | None,
    page_number: int | None,
    chunk_index: int,
    chunk_type: str = "child",
    parent_index: int | None = None,
) -> ChunkData:
    """Create a ChunkData for a table segment.

    Args:
        content: Table content text.
        doc_title: Document title.
        section_heading: Section heading.
        page_number: Page number.
        chunk_index: Sequential chunk index.
        chunk_type: 'parent' or 'child'.
        parent_index: Parent's chunk_index (for children of large tables).

    Returns:
        ChunkData: The chunk data object.
    """
    header = build_header(doc_title, section_heading, page_number)
    return ChunkData(
        content=content,
        content_with_header=f"{header}\n{content}",
        content_hash=_content_hash(content),
        token_count=_token_count(content),
        chunk_index=chunk_index,
        chunk_type=chunk_type,
        parent_index=parent_index,
        section_heading=section_heading,
        page_number=page_number,
    )
