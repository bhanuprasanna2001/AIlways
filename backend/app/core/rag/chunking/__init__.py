"""Chunking package — text splitting into embeddable chunks.

Provides a ``get_chunker`` factory that returns a configured
``Chunker`` instance.

Usage::

    from app.core.rag.chunking import get_chunker

    chunker = get_chunker()
    chunks = chunker.chunk(markdown_text, filename)
"""

from app.core.rag.chunking.base import Chunker, ChunkData
from app.core.rag.chunking.recursive import RecursiveChunker
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_chunker: Chunker | None = None


def get_chunker() -> Chunker:
    """Return the shared chunker instance.

    Lazily initialised on first call using settings from config.

    Returns:
        Chunker: Configured chunker instance.
    """
    global _chunker
    if _chunker is None:
        settings = get_settings()
        _chunker = RecursiveChunker(
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
        )
        logger.info(
            f"Initialised chunker: size={settings.RAG_CHUNK_SIZE}, "
            f"overlap={settings.RAG_CHUNK_OVERLAP}",
        )
    return _chunker


__all__ = ["Chunker", "ChunkData", "RecursiveChunker", "get_chunker"]
