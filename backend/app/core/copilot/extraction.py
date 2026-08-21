"""Statement extraction — pull verifiable factual statements from transcript segments.

Uses Groq's fast inference (same as old claim detector) for near-real-time
extraction. The prompts are domain-agnostic so extraction works for any
document type, not just invoices.

The ``extract_statements`` function is called by the pipeline before
verification graph invocations. It is NOT a LangGraph node — it runs
as a plain async function to keep latency minimal (one LLM call).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid

from groq import AsyncGroq

from app.core.copilot.base import Statement
from app.core.copilot.prompts import (
    EXTRACTION_SYSTEM,
    EXTRACTION_USER_WITH_CONTEXT,
    EXTRACTION_USER_SIMPLE,
)
from app.core.transcription.base import TranscriptSegment
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_statements(
    segments: list[TranscriptSegment],
    context_segments: list[TranscriptSegment] | None = None,
    entity_summary: str = "",
) -> list[Statement]:
    """Extract verifiable factual statements from transcript segments.

    When ``context_segments`` is provided, they are included in the
    prompt for reference resolution. Statements are extracted only
    from ``segments``.

    Args:
        segments: New speaker-diarized transcript segments to analyse.
        context_segments: Prior segments for context (not analysed).
        entity_summary: Running summary of key entities mentioned.

    Returns:
        list[Statement]: Extracted statements with speaker attribution.
    """
    if not segments:
        return []

    # Pre-filter noise: ultra-short and low-confidence segments
    filtered = _filter_segments(segments)
    if not filtered:
        return []

    transcript_text = _format_transcript(filtered)
    if not transcript_text.strip():
        return []

    # Build prompt
    entity_section = ""
    if entity_summary:
        entity_section = f"KNOWN ENTITIES IN THIS CONVERSATION:\n{entity_summary}\n\n"

    if context_segments:
        context_text = _format_transcript(context_segments)
        user_content = EXTRACTION_USER_WITH_CONTEXT.format(
            entity_section=entity_section,
            context=context_text,
            transcript=transcript_text,
        )
    else:
        user_content = EXTRACTION_USER_SIMPLE.format(
            entity_section=entity_section,
            transcript=transcript_text,
        )

    # Call LLM with retry
    raw = await _call_extraction_llm(user_content)
    if raw is None:
        return []

    all_segments = (context_segments or []) + filtered
    return _parse_statements(raw, all_segments)


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------

async def _call_extraction_llm(user_content: str) -> str | None:
    """Call the extraction LLM with exponential-backoff retry.

    Uses Groq by default (fast, free). Falls back gracefully on failure.
    """
    model = SETTINGS.COPILOT.EXTRACTION_MODEL or SETTINGS.GROQ_MODEL
    api_key = SETTINGS.GROQ_API_KEY
    max_retries = SETTINGS.COPILOT.EXTRACTION_MAX_RETRIES
    delay = 1.0

    if not api_key:
        logger.warning("No GROQ_API_KEY configured — statement extraction disabled")
        return None

    client = AsyncGroq(api_key=api_key)

    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": EXTRACTION_SYSTEM},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=SETTINGS.COPILOT.EXTRACTION_TEMPERATURE,
                    response_format={"type": "json_object"},
                ),
                timeout=SETTINGS.API_TIMEOUT_S,
            )
            return response.choices[0].message.content

        except Exception as e:
            is_last = attempt == max_retries
            level = "error" if is_last else "warning"
            getattr(logger, level)(
                f"Extraction LLM attempt {attempt + 1}/{max_retries + 1} "
                f"failed: {e}",
            )
            if is_last:
                return None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_segments(
    segments: list[TranscriptSegment],
) -> list[TranscriptSegment]:
    """Pre-filter segments to remove noise before sending to the LLM.

    Removes ultra-short and low-confidence segments. Short segments
    containing entity anchors (numeric IDs, amounts) are preserved.
    """
    filtered: list[TranscriptSegment] = []
    for seg in segments:
        if seg.confidence < SETTINGS.CLAIM.SEGMENT_MIN_CONFIDENCE:
            continue

        word_count = len(seg.text.split())
        if word_count < SETTINGS.CLAIM.SEGMENT_MIN_WORDS:
            if SETTINGS.CLAIM.SEGMENT_ENTITY_BYPASS and _has_entity_anchor(seg.text):
                filtered.append(seg)
            continue

        filtered.append(seg)
    return filtered


def _has_entity_anchor(text: str) -> bool:
    """Check whether text contains an entity anchor worth preserving."""
    # Numeric IDs (4+ digits)
    if re.search(r"\b\d{4,}\b", text):
        return True
    # Currency amounts
    if re.search(r"[$€£¥][\d,.]+", text):
        return True
    # Common entity keywords (universal — not invoice-specific)
    if re.search(
        r"\b(?:invoice|order|purchase\s*order|po|shipping|delivery|"
        r"receipt|contract|ticket|case|serial|batch|lot|ref|id|number)\b",
        text, re.IGNORECASE,
    ):
        return True
    return False


def _format_transcript(segments: list[TranscriptSegment]) -> str:
    """Format transcript segments into readable speaker-attributed text."""
    lines: list[str] = []
    for seg in segments:
        timestamp = f"[{seg.start:.1f}s - {seg.end:.1f}s]"
        lines.append(f"Speaker {seg.speaker} {timestamp}: {seg.text}")
    return "\n".join(lines)


def _parse_statements(
    raw: str, segments: list[TranscriptSegment],
) -> list[Statement]:
    """Parse LLM JSON response into Statement objects."""
    try:
        data = json.loads(raw)
        items = data.get("statements", [])

        if not items:
            return []

        statements: list[Statement] = []
        for item in items:
            text = item.get("text", "").strip()
            if not text:
                continue

            speaker = item.get("speaker", 0)
            context = item.get("context", "")

            start, end = _find_segment_timing(text, speaker, segments)

            statements.append(Statement(
                id=str(uuid.uuid4()),
                text=text,
                speaker=speaker,
                timestamp_start=start,
                timestamp_end=end,
                context=context,
            ))

        logger.info(f"Extracted {len(statements)} statements from {len(segments)} segments")
        return statements

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse extraction response: {e}")
        return []


def _find_segment_timing(
    text: str, speaker: int, segments: list[TranscriptSegment],
) -> tuple[float, float]:
    """Find the best matching segment's timing for a statement."""
    text_words = set(text.lower().split())
    best_match: TranscriptSegment | None = None
    best_score = 0

    for seg in segments:
        seg_words = set(seg.text.lower().split())
        overlap = len(text_words & seg_words)
        score = overlap + (2 if seg.speaker == speaker else 0)
        if score > best_score:
            best_score = score
            best_match = seg

    if best_match:
        return best_match.start, best_match.end

    if segments:
        return segments[0].start, segments[-1].end

    return 0.0, 0.0
