from app.core.rag.parsing.base import Parser
from app.core.rag.parsing.pdf_parser import PdfParser
from app.core.rag.parsing.txt_parser import TxtParser


_REGISTRY: dict[str, Parser] = {
    "pdf": PdfParser(),
    "txt": TxtParser(),
    "md": TxtParser(),
}


def get_parser(file_type: str) -> Parser:
    """Get a parser for the given file type.

    Args:
        file_type: Lowercase file extension (e.g. 'pdf', 'txt', 'md').

    Returns:
        Parser: The parser instance for this file type.

    Raises:
        ValueError: If the file type is not supported.
    """
    key = file_type.lower().strip()
    if not key:
        raise ValueError("File type cannot be empty")

    parser = _REGISTRY.get(key)
    if parser is None:
        raise ValueError(f"Unsupported file type: '{file_type}'. Supported: {list(_REGISTRY.keys())}")
    return parser
