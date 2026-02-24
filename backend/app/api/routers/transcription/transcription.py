"""Transcription router — pre-recorded and live streaming with claim detection."""

from __future__ import annotations

import asyncio
import re
import time
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, get_db_session
from app.db.models import User
from app.core.auth.deps import get_current_user, require_vault_member
from app.core.config import get_settings
from app.core.transcription import get_transcriber
from app.core.transcription.base import TranscriptSegment
from app.core.claims import get_claim_detector, get_claim_verifier
from app.core.claims.base import Claim
from app.core.logger import setup_logger

from app.api.routers.transcription.schemas import (
    TranscriptionResponse,
    TranscriptSegmentResponse,
    ClaimResponse,
    ClaimVerdictResponse,
    WSTranscriptMessage,
    WSClaimDetectedMessage,
    WSClaimVerifiedMessage,
    WSErrorMessage,
)

logger = setup_logger(__name__)
router = APIRouter(prefix="/vaults/{vault_id}", tags=["transcription"])

SETTINGS = get_settings()

# Supported audio MIME types
_AUDIO_MIMETYPES = {
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mp3", "audio/mpeg",
    "audio/ogg", "audio/webm",
    "audio/flac", "audio/x-flac",
    "audio/mp4", "audio/m4a",
    "audio/aac",
}

_AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".ogg", ".webm", ".flac", ".m4a", ".aac", ".mp4",
}

_MAX_AUDIO_SIZE_MB = 100


# ---------------------------------------------------------------------------
# Pre-recorded transcription (REST)
# ---------------------------------------------------------------------------

@router.post("/transcribe", summary="Transcribe audio and verify claims against vault")
async def transcribe_audio(
    vault_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    """Transcribe a pre-recorded audio file with speaker diarization
    and verify detected claims against the vault's documents.

    Pipeline: transcribe → detect claims → verify each claim against vault.

    Args:
        vault_id: The vault to verify claims against.
        file: The audio file to transcribe.
        current_user: The authenticated user.
        db: The database session.

    Returns:
        TranscriptionResponse: Full transcript with claims and verdicts.
    """
    await require_vault_member(vault_id, current_user, db)

    start = time.monotonic()

    # Validate audio file
    _validate_audio_file(file)
    audio_data = await file.read()
    _validate_audio_size(audio_data)

    mimetype = file.content_type or "audio/wav"

    # 1. Transcribe
    transcriber = get_transcriber()
    transcript = await transcriber.transcribe_file(audio_data, mimetype)

    segments_response = [
        TranscriptSegmentResponse(
            text=s.text,
            speaker=s.speaker,
            start=s.start,
            end=s.end,
            confidence=s.confidence,
        )
        for s in transcript.segments
    ]

    # 2. Detect claims (if enabled and transcript has content)
    claims: list[Claim] = []
    claims_response: list[ClaimResponse] = []
    verdicts_response: list[ClaimVerdictResponse] = []

    if SETTINGS.CLAIM_DETECTION_ENABLED and transcript.segments:
        detector = get_claim_detector()
        claims = await detector.detect_claims(transcript.segments)

        claims_response = [
            ClaimResponse(
                id=c.id,
                text=c.text,
                speaker=c.speaker,
                timestamp_start=c.timestamp_start,
                timestamp_end=c.timestamp_end,
                context=c.context,
            )
            for c in claims
        ]

        # 3. Verify each claim against the vault
        if claims:
            verifier = get_claim_verifier()
            verdicts = await asyncio.gather(*[
                verifier.verify_claim(c, vault_id, db)
                for c in claims
            ])

            verdicts_response = [
                ClaimVerdictResponse(
                    claim_id=v.claim_id,
                    claim_text=v.claim_text,
                    verdict=v.verdict,
                    confidence=v.confidence,
                    explanation=v.explanation,
                    evidence=v.evidence,
                )
                for v in verdicts
            ]

    latency = int((time.monotonic() - start) * 1000)
    logger.info(
        f"Transcription complete: {transcript.speakers} speakers, "
        f"{len(transcript.segments)} segments, {len(claims)} claims, "
        f"{latency}ms",
    )

    return TranscriptionResponse(
        segments=segments_response,
        full_text=transcript.full_text,
        speakers=transcript.speakers,
        duration=transcript.duration,
        claims=claims_response,
        verdicts=verdicts_response,
        latency_ms=latency,
    )


# ---------------------------------------------------------------------------
# Live streaming transcription (WebSocket)
# ---------------------------------------------------------------------------

@router.websocket("/transcribe/live")
async def live_transcribe(
    websocket: WebSocket,
    vault_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """WebSocket endpoint for live audio transcription with real-time
    claim detection and verification.

    Architecture: three concurrent tasks.
      - **Audio sender (main loop):** reads audio from client → forwards to DeepGram.
      - **Receiver task:** reads transcripts from DeepGram → pushes to client.
      - **Flush timer task:** sole claim trigger — polls buffer state.

    Claim detection is **timer-driven only** (no segment-driven trigger).
    The flush timer polls every ``CLAIM_FLUSH_INTERVAL_S`` and fires when:
      - **Idle:** speaker has been silent for ``CLAIM_IDLE_TIMEOUT_S``
        — a natural utterance boundary ensuring all related segments
        (even those split across DeepGram messages) are batched together.
      - **Periodic:** ``CLAIM_BATCH_INTERVAL_S`` has elapsed during
        long continuous speech with enough content accumulated.

    Claims within a batch are verified **concurrently** (parallel
    API calls, each with its own DB session) for multi-speaker
    meeting performance.

    Session end drain: when the user stops or disconnects, DeepGram is
    finalized, remaining segments are drained, and a final claim pass
    runs before the connection closes.

    Protocol:
      - Client sends binary audio chunks.
      - Client sends JSON ``{"type": "stop"}`` to end the stream.
      - Server sends JSON messages: transcript, claim_detected, claim_verified, error.

    Args:
        websocket: The WebSocket connection.
        vault_id: The vault to verify claims against.
        db: The database session (used for auth only).
    """
    # Authenticate via session cookie
    user = await _authenticate_websocket(websocket, db)
    if not user:
        return

    # Verify vault membership
    try:
        await require_vault_member(vault_id, user, db)
    except HTTPException:
        await websocket.close(code=4003, reason="Not a member of this vault")
        return

    await websocket.accept()
    logger.info(f"Live transcription started: vault={vault_id}, user={user.id}")

    # Session state
    buffer = _TranscriptBuffer(vault_id=vault_id)
    claim_tasks: set[asyncio.Task] = set()
    claim_semaphore = asyncio.Semaphore(SETTINGS.CLAIM_MAX_CONCURRENT_TASKS)
    stop_event = asyncio.Event()

    transcriber = get_transcriber()

    # Dynamic sample rate from client (browser AudioContext.sampleRate)
    sample_rate = int(websocket.query_params.get("sample_rate", "16000"))

    try:
        async with transcriber.live_session(sample_rate=sample_rate) as live:

            async def _receiver_loop() -> None:
                """Receive transcripts from DeepGram and push to client.

                This loop is intentionally free of claim-detection logic.
                Triggering claims on individual ``speech_final`` events
                causes premature batching (e.g. a sentence split across
                two DeepGram messages would be split into two batches).
                Instead, the flush timer polls buffer state and fires
                claims when the speaker goes idle or enough time passes.
                """
                while not stop_event.is_set():
                    try:
                        segment = await asyncio.wait_for(live.receive(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break

                    if segment is None:
                        continue

                    buffer.add_segment(segment)

                    msg = WSTranscriptMessage(
                        text=segment.text,
                        speaker=segment.speaker,
                        start=segment.start,
                        end=segment.end,
                        confidence=segment.confidence,
                        is_final=segment.is_final,
                    )
                    try:
                        await websocket.send_json(msg.model_dump())
                    except Exception:
                        stop_event.set()
                        return

            async def _flush_timer_loop() -> None:
                """Sole trigger for claim detection — polls buffer state.

                This is the ONLY mechanism that fires claim detection.
                By not triggering on individual ``speech_final`` events
                in the receiver loop, we ensure that related segments
                (e.g. a sentence split across two DeepGram messages)
                are always batched together.

                Fires when either:
                  - Speaker went idle (``CLAIM_IDLE_TIMEOUT_S`` since
                    last segment) — utterance complete.
                  - Long continuous speech exceeded
                    ``CLAIM_BATCH_INTERVAL_S`` since last check.
                """
                while not stop_event.is_set():
                    await asyncio.sleep(SETTINGS.CLAIM_FLUSH_INTERVAL_S)
                    if stop_event.is_set():
                        break
                    if (
                        SETTINGS.CLAIM_DETECTION_ENABLED
                        and buffer.should_trigger_claims()
                    ):
                        _spawn_claim_task(
                            websocket, buffer, vault_id,
                            claim_tasks, claim_semaphore,
                        )

            # Start background tasks
            receiver_task = asyncio.create_task(_receiver_loop())
            flush_timer_task = asyncio.create_task(_flush_timer_loop())

            # Audio forwarding loop (main loop)
            try:
                while not stop_event.is_set():
                    data = await websocket.receive()

                    if "bytes" in data and data["bytes"]:
                        await live.send_audio(data["bytes"])
                    elif "text" in data and data["text"]:
                        try:
                            import json as json_mod
                            msg_data = json_mod.loads(data["text"])
                            if msg_data.get("type") == "stop":
                                break
                        except (json_mod.JSONDecodeError, TypeError):
                            pass

            except WebSocketDisconnect:
                logger.info(f"Client disconnected: vault={vault_id}, user={user.id}")
            except Exception as e:
                logger.error(f"Live transcription error: {e}")
            finally:
                stop_event.set()

                # --- Session End Drain ---
                # 1. Finalize DeepGram (flush remaining buffered audio)
                await live.finalize()

                # 2. Wait briefly for receiver to drain remaining segments
                try:
                    await asyncio.wait_for(
                        receiver_task,
                        timeout=SETTINGS.CLAIM_DRAIN_TIMEOUT_S,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    receiver_task.cancel()
                    try:
                        await receiver_task
                    except asyncio.CancelledError:
                        pass

                # 3. Stop the flush timer
                flush_timer_task.cancel()
                try:
                    await flush_timer_task
                except asyncio.CancelledError:
                    pass

                # 4. Final claim pass on any remaining unchecked segments
                if (
                    SETTINGS.CLAIM_DETECTION_ENABLED
                    and buffer.has_unchecked()
                ):
                    _spawn_claim_task(
                        websocket, buffer, vault_id,
                        claim_tasks, claim_semaphore,
                    )

    except Exception as e:
        logger.error(f"Failed to start live transcription: {e}")
        try:
            msg = WSErrorMessage(message=f"Failed to start transcription: {e}")
            await websocket.send_json(msg.model_dump())
        except Exception:
            pass

    # Wait for ALL pending claim tasks
    if claim_tasks:
        await asyncio.wait(claim_tasks, timeout=SETTINGS.CLAIM_TASK_TIMEOUT_S)

    logger.info(
        f"Live transcription ended: vault={vault_id}, "
        f"{len(buffer.segments)} segments, {buffer.claims_detected} claims",
    )


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------

def _spawn_claim_task(
    websocket: WebSocket,
    buffer: "_TranscriptBuffer",
    vault_id: UUID,
    claim_tasks: set[asyncio.Task],
    claim_semaphore: asyncio.Semaphore,
) -> None:
    """Capture unchecked segments and create a background claim task.

    The batch is captured **synchronously** at spawn time so that
    the ``_last_check_idx`` pointer advances immediately.  This
    prevents the same segments from being included in a subsequent
    spawn if the timer fires again before the task starts running.

    A semaphore limits the number of concurrent claim tasks to
    ``CLAIM_MAX_CONCURRENT_TASKS``, protecting the external API
    budget (Groq + OpenAI) during fast-paced conversations.

    Args:
        websocket: The WebSocket to push results to.
        buffer: The transcript buffer with context and unchecked segments.
        vault_id: Vault to verify claims against.
        claim_tasks: Set to track active tasks (for cleanup).
        claim_semaphore: Semaphore limiting concurrent API calls.
    """
    context_segments, unchecked = buffer.get_claim_batch()
    if not unchecked:
        return

    entity_summary = buffer.entity_summary

    async def _guarded_process() -> None:
        async with claim_semaphore:
            await _process_claims_batch(
                websocket, buffer, vault_id,
                context_segments, unchecked, entity_summary,
            )

    task = asyncio.create_task(_guarded_process())
    claim_tasks.add(task)
    task.add_done_callback(claim_tasks.discard)


async def _authenticate_websocket(
    websocket: WebSocket, db: AsyncSession,
) -> User | None:
    """Authenticate a WebSocket connection using session cookies or query params.

    Checks for ``session_id`` query parameter first (cross-origin use
    from Streamlit), then falls back to the session cookie.

    Args:
        websocket: The WebSocket connection.
        db: The database session.

    Returns:
        User | None: The authenticated user, or None if auth fails.
    """
    from app.core.tools.redis import get_session

    # Query param takes priority (cross-origin from Streamlit)
    session_id = websocket.query_params.get("session_id")
    if not session_id:
        session_id = websocket.cookies.get(SETTINGS.SESSION_COOKIE_NAME)
    if not session_id:
        await websocket.close(code=4001, reason="Not authenticated")
        return None

    user_id = await get_session(session_id)
    if not user_id:
        await websocket.close(code=4001, reason="Session expired")
        return None

    from sqlmodel import select
    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalars().first()

    if not user:
        await websocket.close(code=4001, reason="User not found")
        return None

    return user


async def _process_claims_batch(
    websocket: WebSocket,
    buffer: "_TranscriptBuffer",
    vault_id: UUID,
    context_segments: list[TranscriptSegment],
    unchecked: list[TranscriptSegment],
    entity_summary: str,
) -> None:
    """Detect claims from a captured batch and verify them concurrently.

    This function receives a pre-captured batch of segments (captured
    at spawn time by ``_spawn_claim_task``) rather than pulling from
    the buffer.  This eliminates race conditions between concurrent
    claim tasks.

    Claims are detected sequentially (one Groq LLM call), then ALL
    claims are verified **concurrently** via ``asyncio.gather`` (each
    with its own DB session).  This is critical for meeting scenarios
    where multiple speakers make several claims in the same batch.

    Args:
        websocket: The WebSocket to push results to.
        buffer: The transcript buffer (for dedup and entity tracking).
        vault_id: Vault to verify claims against.
        context_segments: Prior segments for reference resolution.
        unchecked: New segments to check for claims.
        entity_summary: Running summary of key entities.
    """
    try:
        detector = get_claim_detector()
        claims = await detector.detect_claims(
            segments=unchecked,
            context_segments=context_segments or None,
            entity_summary=entity_summary,
        )

        # Deduplicate against already-seen claims
        new_claims = buffer.deduplicate_claims(claims)
        buffer.claims_detected += len(new_claims)

        # Update entity tracker from all segments (context + new)
        all_text = " ".join(s.text for s in (context_segments or []) + unchecked)
        buffer.update_entities(all_text)

        if not new_claims:
            return

        # 1. Notify ALL claims detected FIRST (fast — no API calls)
        for claim in new_claims:
            detected_msg = WSClaimDetectedMessage(
                claim_id=claim.id,
                text=claim.text,
                speaker=claim.speaker,
            )
            try:
                await websocket.send_json(detected_msg.model_dump())
            except Exception:
                return

        # 2. Verify ALL claims concurrently (each with its own DB session)
        verifier = get_claim_verifier()

        async def _verify_one(claim: Claim):
            try:
                async with get_db_session() as db:
                    return await verifier.verify_claim(claim, vault_id, db)
            except Exception as exc:
                logger.error(f"Verification failed for '{claim.text[:50]}': {exc}")
                return None

        verdicts = await asyncio.gather(*[_verify_one(c) for c in new_claims])

        # 3. Send verification results
        for verdict in verdicts:
            if verdict is None:
                continue
            verified_msg = WSClaimVerifiedMessage(
                claim_id=verdict.claim_id,
                claim_text=verdict.claim_text,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                explanation=verdict.explanation,
                evidence=verdict.evidence,
            )
            try:
                await websocket.send_json(verified_msg.model_dump())
            except Exception:
                return

    except Exception as e:
        logger.error(f"Claim processing error: {e}")


# ---------------------------------------------------------------------------
# Transcript buffer — sliding context window for claim detection
# ---------------------------------------------------------------------------

class _TranscriptBuffer:
    """Manages state for a live transcription session with a sliding
    context window for robust claim detection.

    Key design decisions:

    1. **Context window for reference resolution:** Maintains the last
       ``CLAIM_CONTEXT_SEGMENTS`` checked segments as read-only context.
       When claim detection fires, context + new segments go to the LLM
       so entity references (invoice numbers, etc.) are never lost.

    2. **Timer-only triggering via ``should_trigger_claims()``:**
       A single method decides when to fire claims, based on either
       idle timeout (speaker paused) or periodic interval (long speech).
       No reactive ``speech_final`` trigger — this ensures related
       segments split across DeepGram messages are always batched.

    3. **Duplicate claim prevention:** Tracks seen claim fingerprints
       and deduplicates before emitting to the client.

    Memory is bounded: at most ``CLAIM_MAX_BUFFER_SEGMENTS`` segments
    are stored. Older segments roll off but the entity summary persists.

    Args:
        vault_id: The vault being used for verification.
    """

    def __init__(self, vault_id: UUID) -> None:
        self.vault_id = vault_id
        self.segments: list[TranscriptSegment] = []
        self.claims_detected: int = 0
        self.entity_summary: str = ""

        self._last_check_idx: int = 0
        self._last_check_time: float = time.monotonic()
        self._last_segment_time: float = time.monotonic()
        self._seen_claim_fingerprints: set[str] = set()

    # ------------------------------------------------------------------
    # Segment management
    # ------------------------------------------------------------------

    def add_segment(self, segment: TranscriptSegment) -> None:
        """Store a segment only if it is final.

        Non-final (interim) segments are purely for live UI preview
        and must not pollute the claim-detection buffer.

        Also enforces a memory cap: when segments exceed
        ``CLAIM_MAX_BUFFER_SEGMENTS``, the oldest segments are
        dropped (the check pointer is adjusted accordingly).
        """
        if not segment.is_final:
            return

        self.segments.append(segment)
        self._last_segment_time = time.monotonic()

        # Enforce rolling memory cap
        max_segs = SETTINGS.CLAIM_MAX_BUFFER_SEGMENTS
        if len(self.segments) > max_segs:
            excess = len(self.segments) - max_segs
            self.segments = self.segments[excess:]
            self._last_check_idx = max(0, self._last_check_idx - excess)

    # ------------------------------------------------------------------
    # Claim detection trigger
    # ------------------------------------------------------------------

    def should_trigger_claims(self) -> bool:
        """Whether claim detection should fire now.

        This is the **single** trigger method — called by the flush
        timer.  Returns True when EITHER:

          1. **Idle path:** Speaker has been silent for at least
             ``CLAIM_IDLE_TIMEOUT_S`` AND unchecked segments exist.
             This captures complete utterances naturally — even when
             a sentence is split across multiple DeepGram messages.

          2. **Periodic path:** ``CLAIM_BATCH_INTERVAL_S`` has elapsed
             since the last check AND enough content has accumulated
             (min segments + min chars).  This handles long continuous
             speech without silence gaps.
        """
        if not self.has_unchecked():
            return False

        now = time.monotonic()

        # Path 1: Speaker went idle — natural utterance boundary
        if (now - self._last_segment_time) >= SETTINGS.CLAIM_IDLE_TIMEOUT_S:
            return True

        # Path 2: Periodic check during long continuous speech
        if (now - self._last_check_time) < SETTINGS.CLAIM_BATCH_INTERVAL_S:
            return False

        unchecked = self.segments[self._last_check_idx:]
        if len(unchecked) < SETTINGS.CLAIM_MIN_SEGMENTS:
            return False

        total_chars = sum(len(s.text) for s in unchecked)
        return total_chars >= SETTINGS.CLAIM_MIN_CHARS

    def has_unchecked(self) -> bool:
        """Whether there are segments not yet sent for claim detection."""
        return self._last_check_idx < len(self.segments)

    # ------------------------------------------------------------------
    # Batch preparation
    # ------------------------------------------------------------------

    def get_claim_batch(
        self,
    ) -> tuple[list[TranscriptSegment], list[TranscriptSegment]]:
        """Return (context_segments, unchecked_segments) and advance the pointer.

        ``context_segments`` is a read-only window of the last
        ``CLAIM_CONTEXT_SEGMENTS`` already-checked segments.
        ``unchecked_segments`` are the new segments to check for claims.

        Returns:
            tuple: (context, unchecked) segment lists.
        """
        # Context: last N already-checked segments
        context_start = max(0, self._last_check_idx - SETTINGS.CLAIM_CONTEXT_SEGMENTS)
        context = self.segments[context_start:self._last_check_idx]

        # Unchecked: segments not yet processed
        unchecked = self.segments[self._last_check_idx:]

        # Advance pointer
        self._last_check_idx = len(self.segments)
        self._last_check_time = time.monotonic()

        return context, unchecked

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def deduplicate_claims(self, claims: list[Claim]) -> list[Claim]:
        """Filter out claims that are semantically duplicate of already-seen ones.

        Uses word-overlap ratio to determine duplicates.
        Threshold is ``CLAIM_DEDUP_THRESHOLD`` (default 0.8).

        Args:
            claims: Newly detected claims.

        Returns:
            list[Claim]: Claims that are genuinely new.
        """
        new_claims: list[Claim] = []
        for claim in claims:
            fp = _claim_fingerprint(claim.text)
            if self._is_duplicate(fp):
                logger.debug(f"Duplicate claim skipped: {claim.text[:50]}")
                continue
            self._seen_claim_fingerprints.add(fp)
            new_claims.append(claim)
        return new_claims

    def _is_duplicate(self, fingerprint: str) -> bool:
        """Check if a fingerprint overlaps with any seen fingerprint."""
        fp_words = set(fingerprint.split())
        if not fp_words:
            return False

        for seen_fp in self._seen_claim_fingerprints:
            seen_words = set(seen_fp.split())
            if not seen_words:
                continue
            intersection = len(fp_words & seen_words)
            union = len(fp_words | seen_words)
            if union > 0 and (intersection / union) >= SETTINGS.CLAIM_DEDUP_THRESHOLD:
                return True
        return False

    # ------------------------------------------------------------------
    # Entity tracking
    # ------------------------------------------------------------------

    def update_entities(self, text: str) -> None:
        """Extract key entities from text and update the running summary.

        Uses simple regex patterns to find invoice numbers, order IDs,
        customer IDs, monetary amounts, dates, and product names.
        This lightweight approach avoids an LLM call while providing
        enough context for claim resolution.

        Args:
            text: Combined text from recent segments.
        """
        entities: dict[str, set[str]] = {
            "invoice_numbers": set(),
            "order_ids": set(),
            "amounts": set(),
        }

        # Invoice / order numbers (patterns like "10248", "INV-1234")
        for match in re.finditer(
            r"(?:invoice|order|po|purchase\s*order)\s*(?:number|#|id|no\.?)?\s*:?\s*([A-Z]*-?\d{3,})",
            text,
            re.IGNORECASE,
        ):
            entities["invoice_numbers"].add(match.group(1))

        # Standalone large numbers likely to be IDs (5+ digits)
        for match in re.finditer(r"\b(\d{5,})\b", text):
            entities["order_ids"].add(match.group(1))

        # Monetary amounts
        for match in re.finditer(
            r"\$[\d,]+\.?\d*|\d+[\d,]*\.?\d*\s*(?:dollars|usd)",
            text,
            re.IGNORECASE,
        ):
            entities["amounts"].add(match.group(0))

        # Build summary string
        parts: list[str] = []
        for key, values in entities.items():
            if values:
                label = key.replace("_", " ").title()
                parts.append(f"- {label}: {', '.join(sorted(values))}")

        if parts:
            self.entity_summary = "\n".join(parts)


def _claim_fingerprint(text: str) -> str:
    """Normalize claim text into a comparable fingerprint for dedup.

    Lowercases, strips punctuation, and collapses whitespace so that
    "The total price of invoice 10248 is $440." and
    "the total price of invoice 10248 is 440" are treated as similar.

    Args:
        text: Raw claim text.

    Returns:
        str: Normalized fingerprint string.
    """
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_audio_file(file: UploadFile) -> None:
    """Validate the uploaded audio file type.

    Args:
        file: The uploaded file.

    Raises:
        HTTPException: If the file type is not a supported audio format.
    """
    # Check MIME type
    if file.content_type and file.content_type not in _AUDIO_MIMETYPES:
        # Also check by extension as fallback
        filename = file.filename or ""
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in _AUDIO_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported audio format: {file.content_type}. "
                       f"Supported: wav, mp3, ogg, webm, flac, m4a, aac",
            )


def _validate_audio_size(audio_data: bytes) -> None:
    """Validate the audio file size.

    Args:
        audio_data: Raw audio bytes.

    Raises:
        HTTPException: If the file exceeds the maximum size.
    """
    size_mb = len(audio_data) / (1024 * 1024)
    if size_mb > _MAX_AUDIO_SIZE_MB:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio file too large: {size_mb:.1f}MB. Maximum: {_MAX_AUDIO_SIZE_MB}MB",
        )
    if len(audio_data) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio file is empty",
        )
