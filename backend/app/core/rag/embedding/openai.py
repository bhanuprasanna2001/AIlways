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
        """Embed a batch of texts with exponential-backoff retries.

        Retries on timeout and transient API errors to survive the
        intermittent failures that killed the old one-shot approach.
        """
        if not texts:
            return []
        return await self._embed_with_retries(
            self._client.aembed_documents,
            texts,
            label=f"{len(texts)} texts",
            timeout=SETTINGS.EMBEDDING_TIMEOUT_S,
        )

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

        result = await self._embed_with_retries(
            self._client.aembed_query,
            text,
            label="query",
            timeout=SETTINGS.API_TIMEOUT_S,
        )

        # Store in Redis — fire-and-forget, non-blocking on failure
        try:
            from app.core.tools.redis import get_redis_client
            client = await get_redis_client()
            await client.setex(cache_key, self._cache_ttl, json.dumps(result))
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    async def _embed_with_retries(self, fn, payload, *, label: str, timeout: float):
        """Call *fn(payload)* with exponential-backoff retries.

        On timeout or transient error, retries up to
        ``EMBEDDING_MAX_RETRIES`` times with ``2^attempt`` delay.
        On final failure, raises a descriptive ``TimeoutError``
        (for timeouts) or re-raises the original exception.
        """
        max_retries = max(SETTINGS.EMBEDDING_MAX_RETRIES, 1)
        base_delay = SETTINGS.EMBEDDING_RETRY_BASE_DELAY_S

        for attempt in range(max_retries):
            try:
                return await asyncio.wait_for(fn(payload), timeout=timeout)
            except asyncio.TimeoutError:
                if attempt == max_retries - 1:
                    raise TimeoutError(
                        f"Embedding timed out after {max_retries} attempts "
                        f"({timeout:.0f}s each) for {label}"
                    )
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Embedding timeout for %s (attempt %d/%d), retrying in %.1fs",
                    label, attempt + 1, max_retries, delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                if attempt == max_retries - 1:
                    raise
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Embedding error for %s (attempt %d/%d): %s — retrying in %.1fs",
                    label, attempt + 1, max_retries, exc, delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("Retry loop exhausted without result")
