"""Groq-based claim detector — fast LLM inference for claim extraction."""

from __future__ import annotations

import asyncio
import json
import uuid

from groq import AsyncGroq

from app.core.transcription.base import TranscriptSegment
from app.core.claims.base import Claim
from app.core.claims.exceptions import ClaimDetectionError
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a claim detector for a real-time meeting transcription system. Your job is to extract verifiable factual claims AND data lookup requests from conversation transcripts.

A "claim" is a factual assertion that can be checked against documents. This includes:
- Specific numbers, amounts, prices, quantities
- Dates, deadlines, timelines
- Named entities (products, companies, people, order numbers, invoice numbers)
- Process or procedure assertions ("we always do X")
- Status claims ("the order was shipped", "the payment was received")
- Contractual or policy claims ("the terms state X", "our policy is Y")

A "lookup request" is a question or request for specific data tied to a named entity. This includes:
- "What's the total price of invoice 10248?" → extract as: "the total price of invoice 10248"
- "How many items are in order 10248?" → extract as: "the number of items in order 10248"
- "I want to know the total price of invoice 10248" → extract as: "the total price of invoice 10248"
- "Can you check the shipping date for order 5021?" → extract as: "the shipping date of order 5021"

For lookup requests: Convert the question/request into a neutral declarative phrase (WITHOUT inventing a value). The verification system will retrieve the actual data from documents and include it in the evidence.

Do NOT extract:
- Opinions or subjective statements
- Generic questions without a specific entity reference (e.g. "how does this work?")
- Future predictions or speculation
- Generic greetings or filler speech
- Statements that are clearly hypothetical
- Personal biographical claims (names, education, employment) that would never appear in business documents

CRITICAL RULES:
1. Each claim MUST be self-contained. Always include the full entity reference.
   BAD:  "the total price is $440"  (missing which invoice/order)
   GOOD: "the total price of invoice 10248 is $440"
2. Use PRIOR CONTEXT to resolve references. If the new transcript says "its total price is $440" and the prior context mentions "invoice 10248", output: "the total price of invoice 10248 is $440".
3. If an entity reference cannot be resolved from context, include whatever identifying information is available.
4. Extract claims ONLY from the NEW TRANSCRIPT section. The prior context is for reference resolution only.
5. Normalize ALL numbers: remove thousand separators. Write "10248" not "10,248". Write "$1500" not "$1,500". This is critical because documents store numbers without commas.
6. Only extract claims that can be verified against BUSINESS DOCUMENTS (invoices, purchase orders, shipping records, inventory reports, contracts).
7. When someone asks a question or expresses intent to look up document data (e.g. "what's the total price", "I want to know the shipping date"), ALWAYS extract it as a lookup claim if a specific entity is referenced.

Respond ONLY with valid JSON matching this schema:
{
    "claims": [
        {
            "text": "Self-contained factual claim or lookup phrase with full entity references",
            "speaker": 0,
            "context": "Brief surrounding context including entity references"
        }
    ]
}

If no verifiable claims or lookup requests are found, return: {"claims": []}"""

_USER_TEMPLATE_WITH_CONTEXT = """{entity_section}PRIOR CONTEXT (for reference resolution only — do NOT extract claims from this):
{context}

NEW TRANSCRIPT (extract claims ONLY from this):
{transcript}

Extract all verifiable factual claims from the NEW TRANSCRIPT. Use PRIOR CONTEXT to resolve any references (pronouns, "it", "that invoice", etc.) so each claim is self-contained."""

_USER_TEMPLATE_SIMPLE = """{entity_section}TRANSCRIPT:
{transcript}

Extract all verifiable factual claims from the above transcript."""


class GroqClaimDetector:
    """Extracts verifiable factual claims from transcript segments.

    Uses Groq's fast inference with Llama 3.3 70B for near-real-time
    claim extraction from conversation transcripts.

    Supports a **context window** — prior segments are included in the
    prompt for reference resolution (pronouns, entity co-references)
    but claims are only extracted from new segments.

    Args:
        api_key: Groq API key.
        model: Groq model name (e.g. ``'llama-3.3-70b-versatile'``).
    """

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ClaimDetectionError("Groq API key is required for claim detection")
        self._client = AsyncGroq(api_key=api_key)
        self._model = model
        logger.info(f"Initialised claim detector: model={model}")

    async def detect_claims(
        self,
        segments: list[TranscriptSegment],
        context_segments: list[TranscriptSegment] | None = None,
        entity_summary: str = "",
    ) -> list[Claim]:
        """Extract verifiable factual claims from transcript segments.

        When ``context_segments`` is provided, they are included in the
        prompt as prior context for reference resolution.  Claims are
        extracted only from ``segments``.

        Args:
            segments: New speaker-diarized transcript segments to check.
            context_segments: Prior segments for context (not checked).
            entity_summary: Running summary of key entities mentioned.

        Returns:
            list[Claim]: Extracted claims with speaker attribution
                and timing information.
        """
        if not segments:
            return []

        # Pre-filter: remove ultra-short and low-confidence segments
        filtered = _filter_segments(segments)
        if not filtered:
            return []

        # Build the prompt
        transcript_text = _format_transcript(filtered)
        if not transcript_text.strip():
            return []

        entity_section = ""
        if entity_summary:
            entity_section = f"KNOWN ENTITIES IN THIS CONVERSATION:\n{entity_summary}\n\n"

        if context_segments:
            context_text = _format_transcript(context_segments)
            user_content = _USER_TEMPLATE_WITH_CONTEXT.format(
                entity_section=entity_section,
                context=context_text,
                transcript=transcript_text,
            )
        else:
            user_content = _USER_TEMPLATE_SIMPLE.format(
                entity_section=entity_section,
                transcript=transcript_text,
            )

        # Call Groq with retry
        raw = await self._call_groq(user_content)
        if raw is None:
            return []

        # Include both context + new segments for timing lookup
        all_segments = (context_segments or []) + filtered
        return _parse_claims(raw, all_segments)

    async def _call_groq(self, user_content: str) -> str | None:
        """Call Groq API with exponential-backoff retry.

        Retries on transient failures (rate limits, timeouts, server errors).
        Returns None on permanent failure.

        Args:
            user_content: The user message for the chat completion.

        Returns:
            str | None: Raw JSON response, or None on failure.
        """
        max_retries = SETTINGS.CLAIM_GROQ_MAX_RETRIES
        delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"},
                )
                return response.choices[0].message.content

            except ClaimDetectionError:
                raise
            except Exception as e:
                is_last = attempt == max_retries
                level = "error" if is_last else "warning"
                getattr(logger, level)(
                    f"Groq claim detection attempt {attempt + 1}/{max_retries + 1} "
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

    Removes segments that are:
      - Ultra-short (fewer than ``CLAIM_SEGMENT_MIN_WORDS`` words).
      - Low confidence (below ``CLAIM_SEGMENT_MIN_CONFIDENCE``).

    This reduces Groq token waste and prevents false claims from
    misheard fragments like "Uh", "Yeah", or profanity fragments.

    Args:
        segments: Raw segments from the transcription.

    Returns:
        list[TranscriptSegment]: Filtered segments worth checking.
    """
    filtered: list[TranscriptSegment] = []
    for seg in segments:
        word_count = len(seg.text.split())
        if word_count < SETTINGS.CLAIM_SEGMENT_MIN_WORDS:
            continue
        if seg.confidence < SETTINGS.CLAIM_SEGMENT_MIN_CONFIDENCE:
            continue
        filtered.append(seg)
    return filtered


def _format_transcript(segments: list[TranscriptSegment]) -> str:
    """Format transcript segments into a readable speaker-attributed text.

    Args:
        segments: Speaker-diarized transcript segments.

    Returns:
        str: Formatted transcript text.
    """
    lines: list[str] = []
    for seg in segments:
        timestamp = f"[{seg.start:.1f}s - {seg.end:.1f}s]"
        lines.append(f"Speaker {seg.speaker} {timestamp}: {seg.text}")
    return "\n".join(lines)


def _parse_claims(
    raw: str, segments: list[TranscriptSegment],
) -> list[Claim]:
    """Parse Groq's JSON response into Claim objects.

    Matches each extracted claim to the closest transcript segment
    for timing information.

    Args:
        raw: Raw JSON string from the LLM.
        segments: Original transcript segments for timing lookup.

    Returns:
        list[Claim]: Parsed claims, or empty list if parsing fails.
    """
    try:
        data = json.loads(raw)
        claims_data = data.get("claims", [])

        if not claims_data:
            return []

        claims: list[Claim] = []
        for c in claims_data:
            text = c.get("text", "").strip()
            if not text:
                continue

            speaker = c.get("speaker", 0)
            context = c.get("context", "")

            # Find the best matching segment for timing
            start, end = _find_segment_timing(text, speaker, segments)

            claims.append(Claim(
                id=str(uuid.uuid4()),
                text=text,
                speaker=speaker,
                timestamp_start=start,
                timestamp_end=end,
                context=context,
            ))

        logger.info(f"Detected {len(claims)} claims from {len(segments)} segments")
        return claims

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Failed to parse claim detection response: {e}")
        return []


def _find_segment_timing(
    claim_text: str, speaker: int, segments: list[TranscriptSegment],
) -> tuple[float, float]:
    """Find the best matching segment's timing for a claim.

    Prioritises segments from the same speaker, falling back to
    any segment containing relevant text.

    Args:
        claim_text: The claim text to match.
        speaker: The speaker who made the claim.
        segments: All transcript segments.

    Returns:
        tuple[float, float]: (start, end) timestamps in seconds.
    """
    claim_words = set(claim_text.lower().split())

    best_match: TranscriptSegment | None = None
    best_overlap = 0

    for seg in segments:
        seg_words = set(seg.text.lower().split())
        overlap = len(claim_words & seg_words)

        # Prefer same speaker
        speaker_bonus = 2 if seg.speaker == speaker else 0
        score = overlap + speaker_bonus

        if score > best_overlap:
            best_overlap = score
            best_match = seg

    if best_match:
        return best_match.start, best_match.end

    # Fallback: use first and last segment boundaries
    if segments:
        return segments[0].start, segments[-1].end

    return 0.0, 0.0
