"""Recursive text splitter — LangChain-backed, markdown-aware chunking."""

from __future__ import annotations

import hashlib

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.rag.chunking.base import ChunkData
from app.core.logger import setup_logger

logger = setup_logger(__name__)

_ENCODER = tiktoken.get_encoding("cl100k_base")

_SEPARATORS = [
    "\n\n## ",     # Markdown H2
    "\n\n### ",    # Markdown H3
    "\n\n",        # Paragraph break
    "\n",          # Line break
    ". ",          # Sentence boundary
    " ",           # Word boundary
    "",            # Character fallback
]


class RecursiveChunker:
    """Markdown-aware recursive text splitter.

    Uses LangChain's ``RecursiveCharacterTextSplitter`` with tiktoken
    encoding to split text at token-level boundaries while respecting
    markdown structure.

    Args:
        chunk_size: Maximum chunk size in tokens.
        chunk_overlap: Token overlap between consecutive chunks.
    """

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50) -> None:
        self._splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="cl100k_base",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=_SEPARATORS,
        )

    def chunk(self, text: str, filename: str) -> list[ChunkData]:
        """Split text into chunks with contextual headers.

        Each chunk gets a ``[Source: ...]`` header prepended for better
        embedding quality and BM25 retrieval.

        Args:
            text: Parsed markdown text from the parser.
            filename: Original filename — embedded into chunk headers for
                      search relevance (e.g. ``"invoice_10248.md"`` becomes
                      ``"Source: invoice 10248"``).

        Returns:
            list[ChunkData]: Ordered list of chunks. Empty list if text
            is blank.
        """
        if not text or not text.strip():
            return []

        fragments = self._splitter.split_text(text)
        source_label = _source_label(filename)
        chunks: list[ChunkData] = []

        for fragment in fragments:
            content = fragment.strip()
            if not content:
                continue

            header = f"[Source: {source_label}]"
            content_with_header = f"{header}\n{content}"

            chunks.append(ChunkData(
                content=content,
                content_with_header=content_with_header,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                token_count=len(_ENCODER.encode(content)),
                chunk_index=len(chunks),
            ))

        logger.debug(f"Chunked '{filename}': {len(chunks)} chunk(s) from {len(text)} chars")
        return chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_label(filename: str) -> str:
    """Derive a search-friendly source label from the filename.

    Strips the extension and replaces underscores/hyphens with spaces
    so that BM25 search for ``'invoice 10248'`` matches the header.

    Args:
        filename: Original filename (e.g. ``'invoice_10248.md'``).

    Returns:
        str: Label (e.g. ``'invoice 10248'``).
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return stem.replace("_", " ").replace("-", " ")
