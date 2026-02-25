"""OpenAI embedder — wraps langchain-openai OpenAIEmbeddings."""

from __future__ import annotations

import asyncio
import hashlib
import json

from langchain_openai import OpenAIEmbeddings

from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)
SETTINGS = get_settings()

_CACHE_PREFIX = "emb_cache:"


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
        self._cache_ttl = int(SETTINGS.EMBEDDING_CACHE_TTL_S)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts and return one vector per input."""
        try:
            return await asyncio.wait_for(
                self._client.aembed_documents(texts),
                timeout=SETTINGS.EMBEDDING_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(f"Embedding timed out for {len(texts)} texts")
            raise

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string (cached in Redis with TTL)."""
        cache_key = _CACHE_PREFIX + hashlib.sha256(text.encode()).hexdigest()

        # Try Redis cache — fail-open (miss on error)
        try:
            from app.core.tools.redis import get_redis_client
            client = await get_redis_client()
            raw = await client.get(cache_key)
            if raw is not None:
                return json.loads(raw)
        except Exception:
            pass

        try:
            result = await asyncio.wait_for(
                self._client.aembed_query(text),
                timeout=SETTINGS.API_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error("Query embedding timed out")
            raise

        # Store in Redis — fire-and-forget, non-blocking on failure
        try:
            from app.core.tools.redis import get_redis_client
            client = await get_redis_client()
            await client.setex(cache_key, self._cache_ttl, json.dumps(result))
        except Exception:
            pass

        return result
