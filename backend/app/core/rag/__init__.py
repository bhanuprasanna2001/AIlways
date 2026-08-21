"""RAG pipeline — public API surface.

Re-exports the key factories, functions, and models so callers can do::

    from app.core.rag import get_parser, get_chunker, get_embedder, ...
"""

from app.core.rag.parsing import get_parser, Parser  # noqa: F401
from app.core.rag.chunking import get_chunker, Chunker, ChunkData  # noqa: F401
from app.core.rag.embedding import get_embedder, Embedder  # noqa: F401
from app.core.rag.retrieval import dense_search, sparse_search, hybrid_search, SearchResult  # noqa: F401
from app.core.rag.generation import generate_answer, get_generator, Citation, AnswerResult  # noqa: F401
from app.core.rag.ingest import ingest_document, prepare_document, batch_embed_and_store, PreparedDoc  # noqa: F401
from app.core.rag.exceptions import ParseError, IngestionError  # noqa: F401

__all__ = [
    # Parsing
    "get_parser",
    "Parser",
    # Chunking
    "get_chunker",
    "Chunker",
    "ChunkData",
    # Embedding
    "get_embedder",
    "Embedder",
    # Retrieval
    "dense_search",
    "sparse_search",
    "hybrid_search",
    "SearchResult",
    # Generation
    "generate_answer",
    "get_generator",
    "Citation",
    "AnswerResult",
    # Ingestion
    "ingest_document",
    "prepare_document",
    "batch_embed_and_store",
    "PreparedDoc",
    # Errors
    "ParseError",
    "IngestionError",
]
