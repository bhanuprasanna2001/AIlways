"""Parsing package — document-to-markdown conversion.

Provides a ``get_parser`` factory that returns the appropriate
``Parser`` implementation for a given file type.

Usage::

    from app.core.rag.parsing import get_parser

    parser = get_parser("pdf")
    markdown = await parser.parse(file_bytes, filename)
"""

from app.core.rag.parsing.base import Parser
from app.core.rag.parsing.pdf import PdfParser, _get_pdf_pool
from app.core.rag.parsing.text import TextParser

_PARSERS: dict[str, type] = {
    "pdf": PdfParser,
    "txt": TextParser,
    "md": TextParser,
}


def get_parser(file_type: str) -> Parser:
    """Return a parser instance for the given file type.

    Args:
        file_type: Lowercase file extension (e.g. ``'pdf'``, ``'txt'``, ``'md'``).

    Returns:
        Parser: An instance of the appropriate parser.

    Raises:
        ValueError: If the file type is not supported.
    """
    cls = _PARSERS.get(file_type.lower())
    if cls is None:
        supported = ", ".join(sorted(_PARSERS))
        raise ValueError(f"Unsupported file type '{file_type}'. Supported: {supported}")
    return cls()


def shutdown_pdf_pool() -> None:
    """Shut down the PDF process pool if it was created."""
    try:
        pool = _get_pdf_pool()
        pool.shutdown(wait=False)
    except Exception:
        pass


__all__ = ["Parser", "PdfParser", "TextParser", "get_parser", "shutdown_pdf_pool"]
