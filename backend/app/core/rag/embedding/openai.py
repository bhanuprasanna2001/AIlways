from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.logger import setup_logger

logger = setup_logger(__name__)

# Retry on rate limits and transient errors
_RETRY_EXCEPTIONS = (Exception,)


class OpenAIEmbedder:
    """OpenAI text-embedding-3-large embedder with Matryoshka dimension reduction.

    Uses the OpenAI API to generate embeddings. Supports dimension reduction
    via the `dimensions` parameter (Matryoshka Representation Learning).
    """

    def __init__(self, api_key: str, model: str = "text-embedding-3-large", dims: int = 1536) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dims = dims

    def dimensions(self) -> int:
        """Return the configured embedding dimensions.

        Returns:
            int: Number of dimensions.
        """
        return self._dims

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts using the OpenAI API.

        Args:
            texts: List of texts to embed.

        Returns:
            list[list[float]]: Embedding vectors in the same order as input.
        """
        if not texts:
            return []

        response = await self._client.embeddings.create(
            input=texts,
            model=self._model,
            dimensions=self._dims,
        )

        # Sort by index to guarantee order matches input
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]
