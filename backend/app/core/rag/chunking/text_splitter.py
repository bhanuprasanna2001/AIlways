import re
import hashlib

import tiktoken

from app.core.rag.parsing.ir import ParsedDocument
from app.core.rag.chunking.base import ChunkData
from app.core.rag.chunking.header_builder import build_header


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_TARGET_TOKENS = 300
CHUNK_MAX_TOKENS = 512
OVERLAP_RATIO = 0.12
MIN_CHUNK_TOKENS = 80

_ENCODER = tiktoken.get_encoding("cl100k_base")

# Sentence-boundary regex: split on .!? followed by whitespace, but not
# abbreviations like "Dr.", "U.S.", decimal numbers, etc.
_SENTENCE_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z\"\'\u201c])"
    r"|(?<=\n)\n+"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SentenceChunker:
    """Sentence-aware text chunker.

    Splits a ParsedDocument into chunks that respect sentence boundaries,
    token limits, and overlap requirements. Prepends a contextual header
    to each chunk for embedding.
    """

    def chunk(self, parsed: ParsedDocument) -> list[ChunkData]:
        """Chunk a parsed document into overlapping, sentence-aligned pieces.

        Args:
            parsed: The parsed document intermediate representation.

        Returns:
            list[ChunkData]: Ordered list of chunks with sequential chunk_index.
        """
        doc_title = parsed.metadata.title
        all_chunks: list[ChunkData] = []
        global_char_offset = 0

        for section in parsed.sections:
            if not section.content.strip():
                continue

            sentences = _split_sentences(section.content)
            section_chunks = _build_chunks(
                sentences=sentences,
                doc_title=doc_title,
                section_heading=section.heading,
                page_number=section.page_number,
                char_offset=global_char_offset,
                start_index=len(all_chunks),
            )
            all_chunks.extend(section_chunks)
            global_char_offset += len(section.content)

        return all_chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _token_count(text: str) -> int:
    """Count tokens using the cl100k_base encoding.

    Args:
        text: The text to count tokens for.

    Returns:
        int: Number of tokens.
    """
    return len(_ENCODER.encode(text))


def _content_hash(text: str) -> str:
    """Compute SHA-256 hash of text content.

    Args:
        text: The text to hash.

    Returns:
        str: The hex digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences.

    Args:
        text: The input text.

    Returns:
        list[str]: List of sentence strings.
    """
    parts = _SENTENCE_RE.split(text)
    return [s.strip() for s in parts if s.strip()]


def _build_chunks(
    sentences: list[str],
    doc_title: str | None,
    section_heading: str | None,
    page_number: int | None,
    char_offset: int,
    start_index: int,
) -> list[ChunkData]:
    """Build chunks from a list of sentences with overlap.

    Args:
        sentences: List of sentences from a section.
        doc_title: Document title for header.
        section_heading: Section heading for header.
        page_number: Page number for header.
        char_offset: Character offset in the full document.
        start_index: Starting chunk_index for this batch.

    Returns:
        list[ChunkData]: List of chunk data objects.
    """
    if not sentences:
        return []

    chunks: list[ChunkData] = []
    current_sentences: list[str] = []
    current_tokens = 0
    char_pos = char_offset

    for sentence in sentences:
        sent_tokens = _token_count(sentence)

        # If a single sentence exceeds max, force-split it
        if sent_tokens > CHUNK_MAX_TOKENS:
            # Flush current buffer first
            if current_sentences:
                chunks.append(_make_chunk(
                    sentences=current_sentences,
                    doc_title=doc_title,
                    section_heading=section_heading,
                    page_number=page_number,
                    char_start=char_pos,
                    chunk_index=start_index + len(chunks),
                ))
                char_pos += sum(len(s) + 1 for s in current_sentences)
                current_sentences = []
                current_tokens = 0

            # Force-split the long sentence by words
            words = sentence.split()
            word_buf: list[str] = []
            for word in words:
                test = " ".join(word_buf + [word])
                if _token_count(test) > CHUNK_MAX_TOKENS and word_buf:
                    forced = " ".join(word_buf)
                    chunks.append(_make_chunk(
                        sentences=[forced],
                        doc_title=doc_title,
                        section_heading=section_heading,
                        page_number=page_number,
                        char_start=char_pos,
                        chunk_index=start_index + len(chunks),
                    ))
                    char_pos += len(forced) + 1
                    word_buf = [word]
                else:
                    word_buf.append(word)
            if word_buf:
                current_sentences = [" ".join(word_buf)]
                current_tokens = _token_count(current_sentences[0])
            continue

        # If adding this sentence would exceed target, flush
        if current_tokens + sent_tokens > CHUNK_TARGET_TOKENS and current_sentences:
            chunks.append(_make_chunk(
                sentences=current_sentences,
                doc_title=doc_title,
                section_heading=section_heading,
                page_number=page_number,
                char_start=char_pos,
                chunk_index=start_index + len(chunks),
            ))
            char_pos += sum(len(s) + 1 for s in current_sentences)

            # Compute overlap: keep last few sentences
            overlap_tokens = int(current_tokens * OVERLAP_RATIO)
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
        # Discard if too small, unless it's the only chunk from this section
        if tokens >= MIN_CHUNK_TOKENS or not chunks:
            chunks.append(_make_chunk(
                sentences=current_sentences,
                doc_title=doc_title,
                section_heading=section_heading,
                page_number=page_number,
                char_start=char_pos,
                chunk_index=start_index + len(chunks),
            ))

    return chunks


def _make_chunk(
    sentences: list[str],
    doc_title: str | None,
    section_heading: str | None,
    page_number: int | None,
    char_start: int,
    chunk_index: int,
) -> ChunkData:
    """Create a single ChunkData from accumulated sentences.

    Args:
        sentences: The sentences in this chunk.
        doc_title: Document title for header.
        section_heading: Section heading for header.
        page_number: Page number for header.
        char_start: Starting character offset.
        chunk_index: Sequential index of this chunk.

    Returns:
        ChunkData: The chunk data object.
    """
    content = " ".join(sentences)
    header = build_header(doc_title, section_heading, page_number)
    content_with_header = f"{header}\n{content}"

    return ChunkData(
        content=content,
        content_with_header=content_with_header,
        content_hash=_content_hash(content),
        token_count=_token_count(content),
        chunk_index=chunk_index,
        section_heading=section_heading,
        page_number=page_number,
        char_start=char_start,
        char_end=char_start + len(content),
    )
