"""Text / Markdown parser — handles plain text and markdown files."""

from __future__ import annotations

from app.core.logger import setup_logger

logger = setup_logger(__name__)


class TextParser:
    """Parses plain text and markdown files.

    Decodes raw bytes to a UTF-8 string and normalises line endings.
    """

    async def parse(self, file_content: bytes, filename: str) -> str:
        """Decode text/markdown bytes to a UTF-8 string with normalised line endings."""
        text = file_content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        logger.debug(f"Parsed text file '{filename}': {len(text)} chars")
        return text
