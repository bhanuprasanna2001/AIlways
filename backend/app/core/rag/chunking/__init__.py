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
from app.core.utils import singleton

logger = setup_logger(__name__)


@singleton
def get_chunker() -> Chunker:
    """Return the shared chunker instance (lazily initialised)."""
    settings = get_settings()
    logger.info(
        f"Initialised chunker: size={settings.RAG_CHUNK_SIZE}, "
        f"overlap={settings.RAG_CHUNK_OVERLAP}",
    )
    return RecursiveChunker(
        chunk_size=settings.RAG_CHUNK_SIZE,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    )


__all__ = ["Chunker", "ChunkData", "RecursiveChunker", "get_chunker"]
