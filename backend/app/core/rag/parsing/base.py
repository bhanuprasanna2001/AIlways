from typing import Protocol

from app.core.rag.parsing.ir import ParsedDocument


class Parser(Protocol):
    """Protocol for document parsers.

    Each parser converts raw file bytes into a ParsedDocument IR.
    """

    async def parse(self, file_content: bytes, filename: str) -> ParsedDocument:
        """Parse raw file content into a structured intermediate representation.

        Args:
            file_content: Raw bytes of the file.
            filename: Original filename (used for metadata and format hints).

        Returns:
            ParsedDocument: The parsed intermediate representation.
        """
        ...
