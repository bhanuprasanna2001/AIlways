"""Parser protocol — defines the interface for document parsers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Parser(Protocol):
    """Protocol for document parsers.

    Any class that implements ``parse`` with the correct signature
    satisfies this protocol — no inheritance needed.

    To add a new parser (e.g. DOCX):
        1. Create ``app/core/rag/parsing/docx.py`` with a ``DocxParser`` class
           that has an ``async def parse(self, ...) -> str`` method.
        2. Register it in ``parsing/__init__.py``'s ``_PARSERS`` dict.
    """

    async def parse(self, file_content: bytes, filename: str) -> str:
        """Parse raw file content into markdown text.

        Args:
            file_content: Raw bytes of the uploaded file.
            filename: Original filename (for logging / metadata).

        Returns:
            str: Parsed text in markdown format.
        """
        ...
