from __future__ import annotations

from uuid import UUID

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.base import SearchResult
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


async def entity_id_search(
    entity_ids: list[str],
    vault_id: UUID,
    db: AsyncSession,
) -> list[SearchResult]:
    """Retrieve chunks whose content contains one of the entity IDs.

    Bypasses embedding-based search and does a direct SQL ``ILIKE``
    lookup.  Critical for corpora of near-identical documents (e.g.
    800+ invoices with the same template) where cosine similarity
    cannot distinguish the correct document.

    Args:
        entity_ids: Numeric entity identifiers (e.g. ``["10248"]``).
        vault_id: Scope search to this vault.
        db: Async database session.

    Returns:
        list[SearchResult]: Matching chunks with ``score=1.0``.
            Empty list if no matches or on error.
    """
    if not entity_ids:
        return []
    if not SETTINGS.ENTITY_SEARCH_ENABLED:
        return []

    # Limit the number of IDs to prevent SQL explosion
    ids = entity_ids[: SETTINGS.ENTITY_SEARCH_MAX_IDS]

    try:
        # Build OR conditions for each entity ID
        conditions = " OR ".join(
            f"c.content_with_header ILIKE :id_{i}"
            for i in range(len(ids))
        )
        params: dict = {"vault_id": vault_id, "limit": SETTINGS.ENTITY_SEARCH_LIMIT}
        for i, eid in enumerate(ids):
            params[f"id_{i}"] = f"%{eid}%"

        query = sa_text(f"""
            SELECT c.id, c.doc_id, c.content, c.content_with_header,
                   c.chunk_index, c.section_heading, c.page_number,
                   d.original_filename,
                   c.embedding::text AS embedding_text
            FROM chunks c
            JOIN documents d ON c.doc_id = d.id
            WHERE c.vault_id = :vault_id
              AND c.is_deleted = false
              AND d.status = 'active'
              AND ({conditions})
            ORDER BY c.chunk_index
            LIMIT :limit
        """)

        result = await db.execute(query, params)
        rows = result.fetchall()

        results = [
            SearchResult(
                chunk_id=row.id,
                doc_id=row.doc_id,
                content=row.content,
                content_with_header=row.content_with_header,
                score=1.0,  # Exact match — highest confidence
                section_heading=row.section_heading,
                page_number=row.page_number,
                original_filename=row.original_filename,
                embedding=_parse_embedding(row.embedding_text),
            )
            for row in rows
        ]

        if results:
            logger.info(
                f"Entity search found {len(results)} chunks "
                f"for IDs {ids} in vault {vault_id}"
            )

        return results

    except Exception as exc:
        logger.warning(f"Entity-ID search failed: {exc}")
        return []


def _parse_embedding(raw: str | None) -> list[float] | None:
    """Parse a Postgres vector text representation into a float list."""
    if not raw:
        return None
    try:
        # pgvector format: "[0.1,0.2,0.3,...]"
        cleaned = raw.strip("[]")
        return [float(x) for x in cleaned.split(",")]
    except (ValueError, AttributeError):
        return None
