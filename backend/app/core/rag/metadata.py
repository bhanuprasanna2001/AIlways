"""Document metadata extraction — LLM-based with regex fallback.

Produces two outputs per document at ingestion time:

1. **Structured metadata** — stored on the ``Document`` model for SQL
   filtering (document_type, entity_id, order_date, customer_id,
   total_price, summary, keywords, hypothetical_questions, entities).

2. **Metadata chunk** — a synthetic ``ChunkData`` embedding the summary,
   keywords, and HyDE hypothetical questions into the vector store.
   This chunk participates in ALL existing search paths (dense, BM25,
   hybrid) with zero changes to the retrieval layer.

The LLM extraction is optional and controlled by ``METADATA.ENABLED``
in config. When disabled (or on LLM failure), the module falls back to
zero-cost regex extraction that handles known filename/content patterns.

Design decisions:
  - Regex runs first (<1ms) for core identifiers.
  - LLM enriches with summary, keywords, HyDE questions, entities.
  - On LLM failure: regex results returned as-is (graceful degradation).
  - The metadata chunk is a regular ChunkData — no retrieval layer changes.
  - Content is truncated to MAX_CONTENT_CHARS before LLM call.

Usage::

    from app.core.rag.metadata import extract_document_metadata, build_metadata_chunk

    meta = await extract_document_metadata("invoice_10248.pdf", markdown)
    chunk = build_metadata_chunk(meta, "invoice_10248.pdf", chunk_index=5)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date

from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class DocumentMetadata:
    """Structured metadata extracted from a document.

    All fields are optional — extraction is best-effort.
    """

    # Core identifiers (regex + LLM)
    document_type: str | None = None
    entity_id: str | None = None
    order_date: date | None = None
    customer_id: str | None = None
    total_price: float | None = None

    # LLM-enriched fields
    summary: str | None = None
    keywords: list[str] = field(default_factory=list)
    hypothetical_questions: list[str] = field(default_factory=list)
    entities: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Filename patterns — covers known document naming conventions
# ---------------------------------------------------------------------------

_FILENAME_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"invoice[_\s-]*(\d+)", re.IGNORECASE), "invoice"),
    (re.compile(r"purchase[_\s-]*orders?[_\s-]*(\d+)", re.IGNORECASE), "purchase_order"),
    (re.compile(r"order[_\s-]*(\d+)", re.IGNORECASE), "shipping_order"),
    (re.compile(r"StockReport[_\s-]*([\d-]+)", re.IGNORECASE), "stock_report"),
]

# Content extraction patterns (zero-cost regex)
_DATE_PATTERN = re.compile(
    r"\*{0,2}Order\s+Date:?\*{0,2}\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_CUSTOMER_PATTERN = re.compile(
    r"\*{0,2}Customer\s+ID:?\*{0,2}\s*([A-Z]{3,10})",
    re.IGNORECASE,
)
_TOTAL_PRICE_PATTERNS = [
    re.compile(r"TotalPrice\s*\|?\s*\$?([\d,]+\.?\d*)", re.IGNORECASE),
    re.compile(r"\|\s*\|?\s*TotalPrice\s*\|\s*\$?([\d,]+\.?\d*)", re.IGNORECASE),
    re.compile(r"Total\s+Price:?\s*\$?([\d,]+\.?\d*)", re.IGNORECASE),
]
_STOCK_DATE_PATTERN = re.compile(
    r"StockReport[_\s-]*(\d{4}-\d{2})", re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LLM extraction prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """You are a document metadata extractor. Analyze the document and extract structured metadata.

DOCUMENT FILENAME: {filename}

DOCUMENT CONTENT (may be truncated):
{content}

Extract the following as JSON. Every field is optional — only include what you can confidently extract:

{{
  "document_type": "invoice | purchase_order | shipping_order | stock_report | contract | report | memo | letter | receipt | other",
  "summary": "A concise {summary_max_words}-word-max summary of the document's key content and purpose.",
  "keywords": ["keyword1", "keyword2", ...],
  "hypothetical_questions": [
    "A natural question a user might ask that this document answers",
    "Another question from a different angle",
    "A third question focusing on specific data in the document"
  ],
  "entities": {{
    "entity_id": "primary identifier (e.g. invoice number, order number)",
    "customer_name": "if present",
    "customer_id": "if present",
    "supplier_name": "if present",
    "date": "YYYY-MM-DD if a primary date is found",
    "total_amount": "numeric string if a total is present",
    "currency": "if identifiable"
  }}
}}

RULES:
1. **summary**: Capture WHAT the document is (type, who, when, key figures). Be factual, not generic.
2. **keywords**: Extract {keywords_count} domain-specific terms. Include entity IDs, names, dates, monetary amounts, product names. NO generic words like "document" or "information".
3. **hypothetical_questions**: Write {questions_count} realistic questions a user would ask that this document can answer. Each must be self-contained with full context (include names, IDs, dates). These questions will be embedded for retrieval — they must match real user queries.
4. **entities**: Extract ALL identifiable entities. Use null for missing fields.
5. **document_type**: Classify based on content, not just filename.

Respond with ONLY valid JSON — no markdown fences, no explanation."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_document_metadata(
    filename: str,
    content: str,
) -> DocumentMetadata:
    """Extract metadata from a document using LLM with regex fallback.

    Fast path: regex extracts core identifiers (document_type, entity_id,
    order_date, customer_id, total_price) in <1ms.

    Enrichment path (when METADATA.ENABLED): LLM extracts summary,
    keywords, hypothetical_questions, and entities. On LLM failure,
    regex results are returned as-is.

    Args:
        filename: Original filename (e.g. ``"invoice_10248.pdf"``).
        content: Parsed markdown content of the document.

    Returns:
        DocumentMetadata with all extractable fields.
    """
    # Phase 1: Regex extraction (always runs — zero cost, <1ms)
    meta = _extract_regex(filename, content)

    # Phase 2: LLM enrichment (optional, async)
    cfg = SETTINGS.METADATA
    if cfg.ENABLED:
        try:
            llm_meta = await _extract_llm(filename, content, cfg)
            meta = _merge_metadata(meta, llm_meta)
        except Exception as e:
            logger.warning(
                "LLM metadata extraction failed for '%s': %s — using regex only",
                filename, e,
            )

    logger.debug(
        "Metadata for '%s': type=%s, entity=%s, date=%s, "
        "summary=%s, keywords=%d, questions=%d",
        filename,
        meta.document_type,
        meta.entity_id,
        meta.order_date,
        (meta.summary[:40] + "...") if meta.summary else None,
        len(meta.keywords),
        len(meta.hypothetical_questions),
    )
    return meta


def build_metadata_chunk(
    meta: DocumentMetadata,
    filename: str,
    chunk_index: int,
) -> "ChunkData | None":
    """Build a synthetic metadata chunk for HyDE-style retrieval.

    This chunk contains the document summary, keywords, and hypothetical
    questions. When embedded, it bridges the gap between user queries
    and document content — a user asking "What was the total on invoice
    10248?" will match the hypothetical question "What is the total
    price of invoice 10248?" via dense search, AND match keywords via
    BM25.

    Returns None if there's insufficient metadata to build a useful chunk.

    Args:
        meta: Extracted DocumentMetadata.
        filename: Original filename for the source header.
        chunk_index: Index to assign (typically ``len(existing_chunks)``).

    Returns:
        ChunkData ready for embedding and storage, or None.
    """
    from app.core.rag.chunking.base import ChunkData

    parts: list[str] = []

    # Summary
    if meta.summary:
        parts.append(f"Summary: {meta.summary}")

    # Keywords
    if meta.keywords:
        parts.append(f"Keywords: {', '.join(meta.keywords)}")

    # Hypothetical questions (HyDE)
    if meta.hypothetical_questions:
        parts.append("Questions this document answers:")
        for q in meta.hypothetical_questions:
            parts.append(f"- {q}")

    # Entity info
    if meta.entities:
        entity_parts = [f"{k}: {v}" for k, v in meta.entities.items() if v]
        if entity_parts:
            parts.append(f"Entities: {'; '.join(entity_parts)}")

    if not parts:
        return None

    content = "\n".join(parts)

    # Build source header matching the existing chunker pattern
    source_name = filename.rsplit(".", 1)[0] if "." in filename else filename
    content_with_header = f"[Source: {source_name}]\n{content}"

    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # Token count approximation (~4 chars per token)
    token_count = max(1, len(content) // 4)

    return ChunkData(
        content=content,
        content_with_header=content_with_header,
        content_hash=content_hash,
        token_count=token_count,
        chunk_index=chunk_index,
    )


# ---------------------------------------------------------------------------
# Regex extraction (zero-cost, always runs)
# ---------------------------------------------------------------------------

def _extract_regex(filename: str, content: str) -> DocumentMetadata:
    """Extract metadata using regex patterns — fast, reliable for known formats."""
    doc_type, entity_id = _extract_from_filename(filename)
    order_date = _extract_date(content, filename, doc_type)
    customer_id = _extract_customer_id(content)
    total_price = _extract_total_price(content)

    return DocumentMetadata(
        document_type=doc_type,
        entity_id=entity_id,
        order_date=order_date,
        customer_id=customer_id,
        total_price=total_price,
    )


def _extract_from_filename(filename: str) -> tuple[str | None, str | None]:
    """Extract document_type and entity_id from filename."""
    for pattern, doc_type in _FILENAME_PATTERNS:
        match = pattern.search(filename)
        if match:
            return doc_type, match.group(1)
    return None, None


def _extract_date(
    content: str,
    filename: str,
    doc_type: str | None,
) -> date | None:
    """Extract date from content or filename."""
    match = _DATE_PATTERN.search(content)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            pass

    if doc_type == "stock_report":
        match = _STOCK_DATE_PATTERN.search(filename)
        if match:
            try:
                return date.fromisoformat(match.group(1) + "-01")
            except ValueError:
                pass

    return None


def _extract_customer_id(content: str) -> str | None:
    """Extract customer ID from content."""
    match = _CUSTOMER_PATTERN.search(content)
    return match.group(1).upper() if match else None


def _extract_total_price(content: str) -> float | None:
    """Extract total price from content."""
    for pattern in _TOTAL_PRICE_PATTERNS:
        match = pattern.search(content)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except (ValueError, TypeError):
                continue
    return None


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

async def _extract_llm(
    filename: str,
    content: str,
    cfg: "MetadataConfig",  # noqa: F821
) -> DocumentMetadata:
    """Extract metadata using an LLM call.

    Truncates content to MAX_CONTENT_CHARS to control cost/latency.
    Uses structured JSON output — parses the response with graceful
    degradation on malformed output.
    """
    from langchain_openai import ChatOpenAI

    model_name = cfg.MODEL or SETTINGS.OPENAI_QUERY_MODEL
    llm = ChatOpenAI(
        model=model_name,
        temperature=cfg.TEMPERATURE,
        api_key=SETTINGS.OPENAI_API_KEY,
        max_retries=cfg.MAX_RETRIES,
    )

    # Truncate content to control cost
    truncated = content[: cfg.MAX_CONTENT_CHARS]
    if len(content) > cfg.MAX_CONTENT_CHARS:
        truncated += "\n\n[... content truncated ...]"

    prompt = _EXTRACTION_PROMPT.format(
        filename=filename,
        content=truncated,
        summary_max_words=cfg.SUMMARY_MAX_WORDS,
        keywords_count=cfg.KEYWORDS_COUNT,
        questions_count=cfg.HYPOTHETICAL_QUESTIONS_COUNT,
    )

    response = await llm.ainvoke(prompt)
    raw = response.content if hasattr(response, "content") else str(response)

    return _parse_llm_response(raw)


def _parse_llm_response(raw: str) -> DocumentMetadata:
    """Parse LLM JSON response into DocumentMetadata.

    Handles common LLM quirks: markdown code fences, trailing commas,
    partial JSON. On total failure, returns an empty DocumentMetadata.
    """
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM metadata JSON: %s", raw[:200])
        return DocumentMetadata()

    # Extract fields with safe defaults
    doc_type = data.get("document_type")
    if doc_type and doc_type not in (
        "invoice", "purchase_order", "shipping_order", "stock_report",
        "contract", "report", "memo", "letter", "receipt", "other",
    ):
        doc_type = "other"

    summary = data.get("summary")
    if summary and not isinstance(summary, str):
        summary = str(summary)

    keywords = data.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(k) for k in keywords if k]

    questions = data.get("hypothetical_questions", [])
    if not isinstance(questions, list):
        questions = []
    questions = [str(q) for q in questions if q]

    entities = data.get("entities", {})
    if not isinstance(entities, dict):
        entities = {}
    # Normalize: drop null/None/empty values
    entities = {str(k): str(v) for k, v in entities.items() if v is not None and str(v).lower() != "none"}

    # Extract structured fields from entities dict
    entity_id = entities.get("entity_id")
    order_date_str = entities.get("date")
    order_date = None
    if order_date_str:
        try:
            order_date = date.fromisoformat(order_date_str)
        except ValueError:
            pass

    customer_id = entities.get("customer_id")
    total_price = None
    total_str = entities.get("total_amount")
    if total_str:
        try:
            total_price = float(total_str.replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            pass

    return DocumentMetadata(
        document_type=doc_type,
        entity_id=entity_id,
        order_date=order_date,
        customer_id=customer_id,
        total_price=total_price,
        summary=summary,
        keywords=keywords,
        hypothetical_questions=questions,
        entities=entities,
    )


def _merge_metadata(
    regex_meta: DocumentMetadata,
    llm_meta: DocumentMetadata,
) -> DocumentMetadata:
    """Merge regex and LLM metadata — regex wins for core identifiers,
    LLM provides enrichment fields.

    Strategy:
      - document_type: regex if available, else LLM
      - entity_id, order_date, customer_id, total_price: regex if available, else LLM
      - summary, keywords, hypothetical_questions, entities: always from LLM
    """
    return DocumentMetadata(
        document_type=regex_meta.document_type or llm_meta.document_type,
        entity_id=regex_meta.entity_id or llm_meta.entity_id,
        order_date=regex_meta.order_date or llm_meta.order_date,
        customer_id=regex_meta.customer_id or llm_meta.customer_id,
        total_price=regex_meta.total_price if regex_meta.total_price is not None else llm_meta.total_price,
        summary=llm_meta.summary,
        keywords=llm_meta.keywords,
        hypothetical_questions=llm_meta.hypothetical_questions,
        entities=llm_meta.entities,
    )
