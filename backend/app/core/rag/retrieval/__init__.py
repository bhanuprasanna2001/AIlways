"""Retrieval package — dense, sparse, hybrid, and entity-aware search.

Re-exports public search functions and the ``SearchResult`` model.

Usage::

    from app.core.rag.retrieval import hybrid_search, entity_id_search, SearchResult

    results = await hybrid_search(query_text, query_vec, vault_id, db)
    exact = await entity_id_search(["10248"], vault_id, db)
"""

from app.core.rag.retrieval.base import SearchResult
from app.core.rag.retrieval.dense import dense_search
from app.core.rag.retrieval.sparse import sparse_search
from app.core.rag.retrieval.hybrid import hybrid_search
from app.core.rag.retrieval.entity import entity_id_search

__all__ = [
    "SearchResult",
    "dense_search",
    "sparse_search",
    "hybrid_search",
    "entity_id_search",
]
