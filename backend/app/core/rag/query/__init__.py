"""Query processing package — rewriting and entity extraction.

Provides conversation-aware query rewriting so that follow-up
questions like "who are the customers of it?" are resolved into
standalone queries using prior conversation context.

Usage::

    from app.core.rag.query import rewrite_query, extract_entity_ids

    standalone = await rewrite_query(query, history)
    entity_ids = extract_entity_ids(standalone)
"""

from app.core.rag.query.rewriter import QueryRewriter, rewrite_query, extract_entity_ids

__all__ = ["QueryRewriter", "rewrite_query", "extract_entity_ids"]
