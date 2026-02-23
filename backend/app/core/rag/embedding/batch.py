from app.core.rag.embedding.base import Embedder
from app.core.logger import setup_logger

logger = setup_logger(__name__)

BATCH_SIZE = 100


async def batch_embed(
    texts: list[str],
    content_hashes: list[str],
    embedder: Embedder,
    existing_hashes: set[str] | None = None,
) -> list[list[float] | None]:
    """Embed texts in batches, skipping texts with unchanged content hashes.

    Args:
        texts: List of texts to embed (content_with_header).
        content_hashes: Corresponding content hashes for each text.
        embedder: The embedder implementation to use.
        existing_hashes: Set of content hashes that already have embeddings.

    Returns:
        list[list[float] | None]: Embedding vectors. None for skipped (cached) texts.
    """
    if not texts:
        return []

    existing = existing_hashes or set()
    results: list[list[float] | None] = [None] * len(texts)

    # Identify which texts need embedding
    to_embed: list[tuple[int, str]] = []
    for i, (text, h) in enumerate(zip(texts, content_hashes)):
        if h not in existing:
            to_embed.append((i, text))

    if not to_embed:
        logger.info("All chunks already embedded — skipping API call")
        return results

    logger.info(f"Embedding {len(to_embed)} texts in batches of {BATCH_SIZE}")

    # Process in batches
    for batch_start in range(0, len(to_embed), BATCH_SIZE):
        batch = to_embed[batch_start : batch_start + BATCH_SIZE]
        batch_texts = [text for _, text in batch]

        vectors = await embedder.embed(batch_texts)

        for (original_idx, _), vec in zip(batch, vectors):
            results[original_idx] = vec

    return results
