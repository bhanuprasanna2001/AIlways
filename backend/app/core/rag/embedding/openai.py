"""OpenAI embedder — wraps langchain-openai OpenAIEmbeddings."""

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

from app.core.logger import setup_logger

logger = setup_logger(__name__)


class OpenAIEmbedder:
    """Embedder backed by OpenAI text-embedding models.

    Wraps ``langchain_openai.OpenAIEmbeddings`` to provide a clean,
    provider-agnostic interface that satisfies the ``Embedder`` protocol.

    Connection pooling, retries, and batch splitting are handled by the
    underlying langchain client.

    Args:
        model: OpenAI embedding model name.
        dimensions: Output embedding dimensionality.
        api_key: OpenAI API key.
        batch_size: Maximum texts per API call (default 2048).
    """

    def __init__(
        self,
        model: str,
        dimensions: int,
        api_key: str,
        batch_size: int = 2048,
    ) -> None:
        self._client = OpenAIEmbeddings(
            model=model,
            dimensions=dimensions,
            api_key=api_key,
            chunk_size=batch_size,
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts and return one vector per input."""
        return await self._client.aembed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        return await self._client.aembed_query(text)
