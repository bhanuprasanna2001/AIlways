from typing import Protocol


class Embedder(Protocol):
    """Protocol for embedding services.

    Implementations convert text into dense vector representations.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into vectors.

        Args:
            texts: List of texts to embed.

        Returns:
            list[list[float]]: List of embedding vectors, one per input text.
        """
        ...

    def dimensions(self) -> int:
        """Return the dimensionality of the embedding vectors.

        Returns:
            int: Number of dimensions.
        """
        ...
