import re
import hashlib

import tiktoken

from app.core.rag.parsing.ir import ParsedDocument, ParsedSection
from app.core.rag.chunking.base import ChunkData
from app.core.rag.chunking.header_builder import build_header
from app.core.rag.chunking.table_chunker import chunk_table


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PARENT_TARGET_TOKENS = 1500
PARENT_MAX_TOKENS = 2048
CHILD_TARGET_TOKENS = 300
CHILD_MAX_TOKENS = 512
CHILD_OVERLAP_RATIO = 0.12
CHILD_MIN_TOKENS = 80

_ENCODER = tiktoken.get_encoding("cl100k_base")

_SENTENCE_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z\"\'\u201c])"
    r"|(?<=\n)\n+"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class HierarchicalChunker:
    """Parent-child chunker for improved retrieval precision and reasoning.

    Creates large parent chunks (~1500 tokens) for reasoning context and
    small child chunks (~300 tokens) for retrieval precision. At query time
    child chunks are retrieved (high precision), then expanded to their
    parent context (high quality reasoning).

    Parent chunks are stored but NOT embedded (saves cost).
    Child chunks ARE embedded and ARE retrieved.
    """

    def chunk(self, parsed: ParsedDocument) -> list[ChunkData]:
        """Chunk a parsed document into parent-child hierarchy.

        Args:
            parsed: The parsed document intermediate representation.

        Returns:
            list[ChunkData]: Ordered list of chunks (parents then children).
        """
        doc_title = parsed.metadata.title
        all_chunks: list[ChunkData] = []
        global_char_offset = 0

        for section in parsed.sections:
            # Handle tables first — they use their own chunking strategy
            for table in section.tables:
                if table.content.strip():
                    table_chunks = chunk_table(
                        table_content=table.content,
                        doc_title=doc_title,
                        section_heading=section.heading,
                        page_number=table.page_number or section.page_number,
                        start_index=len(all_chunks),
                    )
                    all_chunks.extend(table_chunks)

            # Skip empty text sections
            text = section.content.strip()
            if not text:
                continue

            tokens = _token_count(text)

            # Small section — single child, no parent needed
            if tokens <= CHILD_MAX_TOKENS:
                child = _make_chunk(
                    content=text,
                    doc_title=doc_title,
                    section_heading=section.heading,
                    section_level=section.level,
                    page_number=section.page_number,
                    char_start=global_char_offset,
                    chunk_index=len(all_chunks),
                    chunk_type="child",
                )
                all_chunks.append(child)
                global_char_offset += len(text)
                continue

            # Large section — create parent + children
            parent_chunks = _build_parents(
                text=text,
                doc_title=doc_title,
                section=section,
                char_offset=global_char_offset,
                start_index=len(all_chunks),
            )

            for parent, children in parent_chunks:
                all_chunks.append(parent)
                all_chunks.extend(children)

            global_char_offset += len(text)

        return all_chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _token_count(text: str) -> int:
    """Count tokens using cl100k_base encoding."""
    return len(_ENCODER.encode(text))


def _content_hash(text: str) -> str:
    """Compute SHA-256 hash of content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    parts = _SENTENCE_RE.split(text)
    return [s.strip() for s in parts if s.strip()]


def _build_parents(
    text: str,
    doc_title: str | None,
    section: ParsedSection,
    char_offset: int,
    start_index: int,
) -> list[tuple[ChunkData, list[ChunkData]]]:
    """Split section text into parent chunks, each with child chunks.

    If the section fits in one parent, creates one parent + its children.
    If it exceeds PARENT_MAX_TOKENS, creates multiple parents.

    Args:
        text: Full section text.
        doc_title: Document title.
        section: The parsed section (for heading/page metadata).
        char_offset: Character offset in the full document.
        start_index: Starting chunk index.

    Returns:
        list of (parent_chunk, [child_chunks]) tuples.
    """
    sentences = _split_sentences(text)
    if not sentences:
        return []

    # Group sentences into parent-sized blocks
    parent_blocks: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        st = _token_count(sentence)
        if current_tokens + st > PARENT_TARGET_TOKENS and current:
            parent_blocks.append(current)
            current = []
            current_tokens = 0
        current.append(sentence)
        current_tokens += st

    if current:
        parent_blocks.append(current)

    results: list[tuple[ChunkData, list[ChunkData]]] = []
    idx = start_index
    char_pos = char_offset

    for block in parent_blocks:
        parent_text = " ".join(block)
        parent_index = idx

        parent = _make_chunk(
            content=parent_text,
            doc_title=doc_title,
            section_heading=section.heading,
            section_level=section.level,
            page_number=section.page_number,
            char_start=char_pos,
            chunk_index=parent_index,
            chunk_type="parent",
        )
        idx += 1

        # Build children within this parent
        children = _build_children(
            sentences=block,
            doc_title=doc_title,
            section_heading=section.heading,
            section_level=section.level,
            page_number=section.page_number,
            char_offset=char_pos,
            start_index=idx,
            parent_index=parent_index,
        )
        idx += len(children)
        char_pos += len(parent_text)

        results.append((parent, children))

    return results


def _build_children(
    sentences: list[str],
    doc_title: str | None,
    section_heading: str | None,
    section_level: int | None,
    page_number: int | None,
    char_offset: int,
    start_index: int,
    parent_index: int,
) -> list[ChunkData]:
    """Build child chunks from sentences with overlap.

    Args:
        sentences: Sentences belonging to one parent block.
        doc_title: Document title.
        section_heading: Section heading.
        section_level: Section level.
        page_number: Page number.
        char_offset: Character offset.
        start_index: Starting chunk index.
        parent_index: Parent's chunk index for reference.

    Returns:
        list[ChunkData]: Child chunks.
    """
    if not sentences:
        return []

    chunks: list[ChunkData] = []
    current_sentences: list[str] = []
    current_tokens = 0
    char_pos = char_offset

    for sentence in sentences:
        sent_tokens = _token_count(sentence)

        # Single sentence exceeds max → force-split by words
        if sent_tokens > CHILD_MAX_TOKENS:
            if current_sentences:
                chunks.append(_make_chunk(
                    content=" ".join(current_sentences),
                    doc_title=doc_title,
                    section_heading=section_heading,
                    section_level=section_level,
                    page_number=page_number,
                    char_start=char_pos,
                    chunk_index=start_index + len(chunks),
                    chunk_type="child",
                    parent_index=parent_index,
                ))
                char_pos += sum(len(s) + 1 for s in current_sentences)
                current_sentences = []
                current_tokens = 0

            # Force-split
            words = sentence.split()
            word_buf: list[str] = []
            for word in words:
                test = " ".join(word_buf + [word])
                if _token_count(test) > CHILD_MAX_TOKENS and word_buf:
                    forced = " ".join(word_buf)
                    chunks.append(_make_chunk(
                        content=forced,
                        doc_title=doc_title,
                        section_heading=section_heading,
                        section_level=section_level,
                        page_number=page_number,
                        char_start=char_pos,
                        chunk_index=start_index + len(chunks),
                        chunk_type="child",
                        parent_index=parent_index,
                    ))
                    char_pos += len(forced) + 1
                    word_buf = [word]
                else:
                    word_buf.append(word)
            if word_buf:
                current_sentences = [" ".join(word_buf)]
                current_tokens = _token_count(current_sentences[0])
            continue

        # Flush if adding this sentence exceeds target
        if current_tokens + sent_tokens > CHILD_TARGET_TOKENS and current_sentences:
            chunks.append(_make_chunk(
                content=" ".join(current_sentences),
                doc_title=doc_title,
                section_heading=section_heading,
                section_level=section_level,
                page_number=page_number,
                char_start=char_pos,
                chunk_index=start_index + len(chunks),
                chunk_type="child",
                parent_index=parent_index,
            ))
            char_pos += sum(len(s) + 1 for s in current_sentences)

            # Compute overlap
            overlap_tokens = int(current_tokens * CHILD_OVERLAP_RATIO)
            overlap_sentences: list[str] = []
            overlap_count = 0
            for s in reversed(current_sentences):
                st = _token_count(s)
                if overlap_count + st > overlap_tokens:
                    break
                overlap_sentences.insert(0, s)
                overlap_count += st

            current_sentences = overlap_sentences
            current_tokens = overlap_count

        current_sentences.append(sentence)
        current_tokens += sent_tokens

    # Flush remaining
    if current_sentences:
        tokens = _token_count(" ".join(current_sentences))
        if tokens >= CHILD_MIN_TOKENS or not chunks:
            chunks.append(_make_chunk(
                content=" ".join(current_sentences),
                doc_title=doc_title,
                section_heading=section_heading,
                section_level=section_level,
                page_number=page_number,
                char_start=char_pos,
                chunk_index=start_index + len(chunks),
                chunk_type="child",
                parent_index=parent_index,
            ))

    return chunks


def _make_chunk(
    content: str,
    doc_title: str | None,
    section_heading: str | None,
    section_level: int | None,
    page_number: int | None,
    char_start: int,
    chunk_index: int,
    chunk_type: str = "child",
    parent_index: int | None = None,
) -> ChunkData:
    """Create a single ChunkData with contextual header.

    Args:
        content: The chunk text content.
        doc_title: Document title.
        section_heading: Section heading.
        section_level: Section heading level.
        page_number: Page number.
        char_start: Starting character offset.
        chunk_index: Sequential chunk index.
        chunk_type: 'parent' or 'child'.
        parent_index: Parent's chunk_index (for child chunks).

    Returns:
        ChunkData: The chunk data object.
    """
    header = build_header(doc_title, section_heading, page_number)
    return ChunkData(
        content=content,
        content_with_header=f"{header}\n{content}",
        content_hash=_content_hash(content),
        token_count=_token_count(content),
        chunk_index=chunk_index,
        chunk_type=chunk_type,
        parent_index=parent_index,
        section_heading=section_heading,
        section_level=section_level,
        page_number=page_number,
        char_start=char_start,
        char_end=char_start + len(content),
    )
