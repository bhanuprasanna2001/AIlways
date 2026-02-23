from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag.retrieval.filters import SearchResult
from app.core.logger import setup_logger

logger = setup_logger(__name__)


async def expand_to_parents(
    child_results: list[SearchResult],
    db: AsyncSession,
) -> list[SearchResult]:
    """Fetch parent chunk content for each child result.

    At query time, child chunks provide retrieval precision but parents
    provide richer reasoning context. This function enriches each child
    result with its parent's content. Deduplicates shared parents so
    one parent is fetched only once even if multiple children reference it.

    Children without a parent (orphans or standalone chunks) are returned
    as-is with parent_content = None.

    Args:
        child_results: Search results (child chunks) to expand.
        db: The database session.

    Returns:
        list[SearchResult]: Results with parent_content populated.
    """
    if not child_results:
        return []

    # Collect chunk_ids to look up their parent_chunk_id
    chunk_ids = [str(r.chunk_id) for r in child_results]

    # Fetch parent_chunk_id for each child
    query = text("""
        SELECT c.id, c.parent_chunk_id
        FROM chunks c
        WHERE c.id = ANY(:chunk_ids)
    """)
    result = await db.execute(query, {"chunk_ids": chunk_ids})

    child_to_parent: dict[str, str | None] = {}
    parent_ids: set[str] = set()
    for row in result.fetchall():
        child_id = str(row[0])
        parent_id = str(row[1]) if row[1] else None
        child_to_parent[child_id] = parent_id
        if parent_id:
            parent_ids.add(parent_id)

    # Fetch parent content in one query
    parent_content_map: dict[str, str] = {}
    if parent_ids:
        parent_query = text("""
            SELECT id, content_with_header
            FROM chunks
            WHERE id = ANY(:parent_ids)
              AND is_deleted = FALSE
        """)
        parent_result = await db.execute(parent_query, {"parent_ids": list(parent_ids)})
        for row in parent_result.fetchall():
            parent_content_map[str(row[0])] = row[1]

    # Enrich results
    expanded = []
    for r in child_results:
        cid = str(r.chunk_id)
        parent_id = child_to_parent.get(cid)
        parent_text = parent_content_map.get(parent_id) if parent_id else None
        expanded.append(r.model_copy(update={"parent_content": parent_text}))

    logger.info(f"Parent expansion: {len(parent_content_map)} parents for {len(child_results)} children")
    return expanded
