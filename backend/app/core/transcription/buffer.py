"""Transcript buffer — sliding context window for statement extraction.

Key design:
1. Context window of last N checked segments for entity reference resolution.
2. Timer-only triggering via ``should_trigger_claims()`` — no reactive
   ``speech_final`` trigger, ensuring related segments split across
   DeepGram messages are always batched together.
3. Duplicate statement prevention via word-overlap fingerprinting.
4. Memory bounded at ``CLAIM_MAX_BUFFER_SEGMENTS``.
"""

from __future__ import annotations

import re
import time
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.core.transcription.base import TranscriptSegment
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


@runtime_checkable
class _HasText(Protocol):
    """Any object with a ``text`` attribute — both Claim and Statement satisfy."""
    text: str


class TranscriptBuffer:
    """Manages state for a live transcription session."""

    def __init__(self, vault_id: UUID) -> None:
        self.vault_id = vault_id
        self.segments: list[TranscriptSegment] = []
        self.claims_detected: int = 0
        self.entity_summary: str = ""

        self._last_check_idx: int = 0
        self._last_check_time: float = time.monotonic()
        self._last_segment_time: float = time.monotonic()
        self._seen_claim_fingerprints: set[str] = set()

    # Segment management ------------------------------------------------------

    def add_segment(self, segment: TranscriptSegment) -> None:
        """Store a final segment. Interim segments are ignored."""
        if not segment.is_final:
            return

        self.segments.append(segment)
        self._last_segment_time = time.monotonic()

        # Enforce rolling memory cap
        max_segs = SETTINGS.CLAIM.MAX_BUFFER_SEGMENTS
        if len(self.segments) > max_segs:
            excess = len(self.segments) - max_segs
            self.segments = self.segments[excess:]
            self._last_check_idx = max(0, self._last_check_idx - excess)

    # Claim detection trigger -------------------------------------------------

    def should_trigger_claims(self) -> bool:
        """Whether claim detection should fire now.

        Returns True when EITHER:
          1. Speaker went idle for ``CLAIM_IDLE_TIMEOUT_S`` (natural boundary).
          2. ``CLAIM_BATCH_INTERVAL_S`` elapsed with enough accumulated content.
        """
        if not self.has_unchecked():
            return False

        now = time.monotonic()

        # Path 1: Speaker went idle
        if (now - self._last_segment_time) >= SETTINGS.CLAIM.IDLE_TIMEOUT_S:
            return True

        # Path 2: Periodic check during long continuous speech
        if (now - self._last_check_time) < SETTINGS.CLAIM.BATCH_INTERVAL_S:
            return False

        unchecked = self.segments[self._last_check_idx:]
        if len(unchecked) < SETTINGS.CLAIM.MIN_SEGMENTS:
            return False

        return sum(len(s.text) for s in unchecked) >= SETTINGS.CLAIM.MIN_CHARS

    def has_unchecked(self) -> bool:
        """Whether there are segments not yet sent for claim detection."""
        return self._last_check_idx < len(self.segments)

    # Batch preparation -------------------------------------------------------

    def get_claim_batch(
        self,
    ) -> tuple[list[TranscriptSegment], list[TranscriptSegment]]:
        """Return (context_segments, unchecked_segments) and advance the pointer."""
        context_start = max(0, self._last_check_idx - SETTINGS.CLAIM.CONTEXT_SEGMENTS)
        context = self.segments[context_start:self._last_check_idx]
        unchecked = self.segments[self._last_check_idx:]

        self._last_check_idx = len(self.segments)
        self._last_check_time = time.monotonic()
        return context, unchecked

    # Deduplication -----------------------------------------------------------

    def deduplicate_claims(self, claims: list) -> list:
        """Filter out statements/claims that are semantically duplicate of already-seen ones.

        Accepts any list of objects with a ``.text`` attribute (both
        ``Statement`` and ``Claim`` models satisfy this).
        """
        new_claims: list = []
        for claim in claims:
            fp = claim_fingerprint(claim.text)
            if self._is_duplicate(fp):
                logger.debug(f"Duplicate skipped: {claim.text[:50]}")
                continue
            self._seen_claim_fingerprints.add(fp)
            new_claims.append(claim)
        return new_claims

    def _is_duplicate(self, fingerprint: str) -> bool:
        fp_words = set(fingerprint.split())
        if not fp_words:
            return False
        for seen_fp in self._seen_claim_fingerprints:
            seen_words = set(seen_fp.split())
            if not seen_words:
                continue
            intersection = len(fp_words & seen_words)
            union = len(fp_words | seen_words)
            if union > 0 and (intersection / union) >= SETTINGS.CLAIM.DEDUP_THRESHOLD:
                return True
        return False

    # Entity tracking ---------------------------------------------------------

    def update_entities(self, text: str) -> None:
        """Extract key entities from text and merge into the running summary.

        Uses simple regex patterns to avoid an LLM call while providing
        enough context for statement resolution.

        IMPORTANT: Merges new entities with existing ones instead of
        overwriting, so previously mentioned entities are never lost.

        Universal: works for invoices, orders, contracts, serial numbers,
        case IDs, and other common business identifiers.
        """
        entities: dict[str, set[str]] = {
            "reference_numbers": set(),
            "numeric_ids": set(),
            "amounts": set(),
        }

        # Parse existing summary back into sets for merging
        for line in self.entity_summary.split("\n"):
            line = line.strip().lstrip("- ")
            if line.startswith("Reference Numbers:"):
                for v in line.split(":", 1)[1].split(","):
                    v = v.strip()
                    if v:
                        entities["reference_numbers"].add(v)
            elif line.startswith("Numeric Ids:"):
                for v in line.split(":", 1)[1].split(","):
                    v = v.strip()
                    if v:
                        entities["numeric_ids"].add(v)
            elif line.startswith("Amounts:"):
                for v in line.split(":", 1)[1].split(","):
                    v = v.strip()
                    if v:
                        entities["amounts"].add(v)

        # Extract reference numbers with entity keywords
        for match in re.finditer(
            r"(?:invoice|order|po|purchase\s*order|contract|ticket|case|"
            r"serial|batch|lot|ref|shipment|delivery|receipt)"
            r"\s*(?:number|#|id|no\.?)?\s*:?\s*([A-Z]*-?\d{3,})",
            text, re.IGNORECASE,
        ):
            entities["reference_numbers"].add(match.group(1))

        # Bare numeric IDs (5+ digits to avoid false positives)
        for match in re.finditer(r"\b(\d{5,})\b", text):
            entities["numeric_ids"].add(match.group(1))

        # Currency amounts (multi-currency)
        for match in re.finditer(
            r"[$€£¥][\d,]+\.?\d*|\d+[\d,]*\.?\d*\s*(?:dollars|usd|eur|gbp)",
            text, re.IGNORECASE,
        ):
            entities["amounts"].add(match.group(0))

        parts: list[str] = []
        for key, values in entities.items():
            if values:
                label = key.replace("_", " ").title()
                parts.append(f"- {label}: {', '.join(sorted(values))}")

        if parts:
            self.entity_summary = "\n".join(parts)[:2000]


def claim_fingerprint(text: str) -> str:
    """Normalize claim text into a comparable fingerprint for dedup."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())
