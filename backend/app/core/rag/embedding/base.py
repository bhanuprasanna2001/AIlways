"""Embedder protocol — defines the interface for embedding providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Protocol for embedding providers.

    Any class that implements ``embed_documents`` and ``embed_query``
    with the correct signatures satisfies this protocol.

    To add a new provider (e.g. Cohere):
        1. Create ``app/core/rag/embedding/cohere.py`` with a
           ``CohereEmbedder`` class.
        2. Update ``get_embedder()`` in ``embedding/__init__.py``.
    """

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            list[list[float]]: Embedding vectors, one per input text.
        """
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string.

        Args:
            text: Query text to embed.

        Returns:
            list[float]: Embedding vector.
        """
        ...
