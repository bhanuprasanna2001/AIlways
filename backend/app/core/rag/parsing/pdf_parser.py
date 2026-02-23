import re
import pymupdf4llm
import pymupdf

from app.core.rag.parsing.ir import (
    ParsedDocument,
    ParsedSection,
    ParsedTable,
    DocumentMetadata,
)
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class PdfParser:
    """Parser for PDF files using pymupdf4llm.

    Converts PDF bytes into a ParsedDocument with sections, tables, and metadata.
    """

    async def parse(self, file_content: bytes, filename: str) -> ParsedDocument:
        """Parse PDF bytes into a ParsedDocument.

        Args:
            file_content: Raw PDF bytes.
            filename: Original filename.

        Returns:
            ParsedDocument: The parsed intermediate representation.

        Raises:
            ValueError: If the PDF cannot be opened.
        """
        warnings: list[str] = []

        try:
            doc = pymupdf.open(stream=file_content, filetype="pdf")
        except Exception as e:
            raise ValueError(f"Cannot open PDF: {e}")

        # Extract metadata
        meta = doc.metadata or {}
        metadata = DocumentMetadata(
            title=meta.get("title") or _title_from_filename(filename),
            author=meta.get("author"),
            created_date=meta.get("creationDate"),
            page_count=len(doc),
        )

        # Extract markdown via pymupdf4llm
        try:
            md_text = pymupdf4llm.to_markdown(doc)
        except Exception as e:
            warnings.append(f"Markdown extraction failed: {e}")
            md_text = ""

        if not md_text.strip():
            warnings.append("No text content extracted from PDF")

        doc.close()

        # Parse markdown into sections
        sections = _split_markdown_into_sections(md_text)

        return ParsedDocument(
            doc_id=filename,
            sections=sections,
            metadata=metadata,
            raw_text=md_text,
            parse_warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)


def _title_from_filename(filename: str) -> str:
    """Derive a title from the filename by stripping extension.

    Args:
        filename: The original filename.

    Returns:
        str: A human-readable title.
    """
    return filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")


def _split_markdown_into_sections(md_text: str) -> list[ParsedSection]:
    """Split markdown text into sections based on heading markers.

    Args:
        md_text: Full markdown text.

    Returns:
        list[ParsedSection]: List of sections with heading, level, and content.
    """
    if not md_text.strip():
        return []

    sections: list[ParsedSection] = []
    lines = md_text.split("\n")
    current_heading: str | None = None
    current_level = 0
    current_lines: list[str] = []
    current_page: int | None = None

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            # Flush previous section
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    sections.append(ParsedSection(
                        heading=current_heading,
                        level=current_level,
                        content=content,
                        page_number=current_page,
                    ))

            current_heading = match.group(2).strip()
            current_level = len(match.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    # Flush final section
    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(ParsedSection(
                heading=current_heading,
                level=current_level,
                content=content,
                page_number=current_page,
            ))

    # If no headings were found, return the entire text as one section
    if not sections and md_text.strip():
        sections.append(ParsedSection(
            heading=None,
            level=0,
            content=md_text.strip(),
            page_number=None,
        ))

    return sections
