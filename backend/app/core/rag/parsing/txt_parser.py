import re

from app.core.rag.parsing.ir import (
    ParsedDocument,
    ParsedSection,
    DocumentMetadata,
)


class TxtParser:
    """Parser for plain text and Markdown files.

    Splits content by Markdown headings if present, otherwise treats
    the entire content as a single section.
    """

    async def parse(self, file_content: bytes, filename: str) -> ParsedDocument:
        """Parse text or Markdown bytes into a ParsedDocument.

        Args:
            file_content: Raw file bytes.
            filename: Original filename.

        Returns:
            ParsedDocument: The parsed intermediate representation.
        """
        warnings: list[str] = []

        # Decode with BOM stripping and line-ending normalization
        text = file_content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")

        if not text.strip():
            warnings.append("File is empty or contains only whitespace")

        title = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        sections = _split_text_into_sections(text)

        return ParsedDocument(
            doc_id=filename,
            sections=sections,
            metadata=DocumentMetadata(title=title, page_count=1),
            raw_text=text,
            parse_warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)


def _split_text_into_sections(text: str) -> list[ParsedSection]:
    """Split text into sections based on Markdown heading markers.

    Args:
        text: The full input text.

    Returns:
        list[ParsedSection]: Sections with heading, level, and content.
    """
    if not text.strip():
        return []

    sections: list[ParsedSection] = []
    lines = text.split("\n")
    current_heading: str | None = None
    current_level = 0
    current_lines: list[str] = []

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
            ))

    # No headings found — single section
    if not sections and text.strip():
        sections.append(ParsedSection(
            heading=None,
            level=0,
            content=text.strip(),
        ))

    return sections
