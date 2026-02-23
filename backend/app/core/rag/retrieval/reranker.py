from typing import Protocol

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.rag.retrieval.filters import SearchResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)


class Reranker(Protocol):
    """Protocol for cross-encoder reranking services."""

    async def rerank(
        self, query: str, results: list[SearchResult], top_k: int = 10
    ) -> list[SearchResult]:
        """Re-score and reorder search results using a cross-encoder.

        Args:
            query: The original query text.
            results: Candidate search results to rerank.
            top_k: Maximum number of results to return.

        Returns:
            list[SearchResult]: Reranked results sorted by reranker score.
        """
        ...


class CohereReranker:
    """Cross-encoder reranker using the Cohere Rerank API.

    Cross-encoders jointly encode query + document, capturing interaction
    signals (negation, conditionals) that bi-encoders miss. Typically
    improves precision by 15-25% over bi-encoder ranking alone.
    """

    def __init__(self, api_key: str, model: str = "rerank-v3.5") -> None:
        if not api_key:
            raise ValueError("Cohere API key is required")

        import cohere
        self._client = cohere.AsyncClientV2(api_key=api_key)
        self._model = model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def rerank(
        self, query: str, results: list[SearchResult], top_k: int = 10
    ) -> list[SearchResult]:
        """Rerank results via Cohere cross-encoder.

        Args:
            query: The original query text.
            results: Candidate search results.
            top_k: Maximum results to return.

        Returns:
            list[SearchResult]: Reranked and truncated results.
        """
        if not results:
            return []

        # Cohere has a 4096 token limit per document — truncate long content
        documents = [r.content_with_header[:4000] for r in results]

        try:
            response = await self._client.rerank(
                model=self._model,
                query=query,
                documents=documents,
                top_n=min(top_k, len(results)),
            )
        except Exception as e:
            logger.warning(f"Cohere rerank failed, returning original order: {e}")
            return results[:top_k]

        reranked = []
        for item in response.results:
            original = results[item.index]
            reranked.append(
                original.model_copy(update={"score": item.relevance_score})
            )

        return reranked


class NoOpReranker:
    """Pass-through reranker for when no reranking API is configured.

    Returns results in their original order, truncated to top_k.
    Ensures the pipeline works in dev/test without a Cohere key.
    """

    async def rerank(
        self, query: str, results: list[SearchResult], top_k: int = 10
    ) -> list[SearchResult]:
        """Return results unchanged, truncated to top_k.

        Args:
            query: The original query text (unused).
            results: Candidate search results.
            top_k: Maximum results to return.

        Returns:
            list[SearchResult]: Original results truncated to top_k.
        """
        return results[:top_k]


def get_reranker(api_key: str) -> CohereReranker | NoOpReranker:
    """Factory to create the appropriate reranker based on configuration.

    Args:
        api_key: Cohere API key. Empty string means no reranking.

    Returns:
        CohereReranker if key is provided, NoOpReranker otherwise.
    """
    if api_key:
        logger.info("Cohere reranker enabled")
        return CohereReranker(api_key=api_key)

    logger.info("No Cohere API key — using NoOp reranker")
    return NoOpReranker()
