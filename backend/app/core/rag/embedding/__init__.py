"""Embedding package — text-to-vector conversion.

Provides a ``get_embedder`` factory that returns a shared
``Embedder`` instance (singleton).

Usage::

    from app.core.rag.embedding import get_embedder

    embedder = get_embedder()
    vectors = await embedder.embed_documents(texts)
    query_vec = await embedder.embed_query(query)
"""

from app.core.rag.embedding.base import Embedder
from app.core.rag.embedding.openai import OpenAIEmbedder
from app.core.config import get_settings
from app.core.logger import setup_logger
from app.core.utils import singleton

logger = setup_logger(__name__)


@singleton
def get_embedder() -> Embedder:
    """Return the shared embedder instance (lazily initialised)."""
    settings = get_settings()
    logger.info(
        f"Initialised embedder: model={settings.OPENAI_EMBEDDING_MODEL}, "
        f"dims={settings.OPENAI_EMBEDDING_DIMENSIONS}",
    )
    return OpenAIEmbedder(
        model=settings.OPENAI_EMBEDDING_MODEL,
        dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
        api_key=settings.OPENAI_API_KEY,
        batch_size=settings.RAG_EMBEDDING_BATCH_SIZE,
    )


__all__ = ["Embedder", "OpenAIEmbedder", "get_embedder"]
