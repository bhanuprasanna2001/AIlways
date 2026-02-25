"""Transcription router — pre-recorded and live streaming with claim detection."""

from __future__ import annotations

import asyncio
import json as json_mod
import time
from uuid import UUID

from fastapi import (
    APIRouter, Depends, UploadFile, File, HTTPException,
    WebSocket, WebSocketDisconnect, status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from app.db import get_db, get_db_session
from app.db.models import User
from app.db.models.transcription_session import TranscriptionSession
from app.core.auth.deps import get_current_user, require_vault_member, authenticate_websocket
from app.core.config import get_settings
from app.core.transcription import get_transcriber
from app.core.transcription.persistence import SessionPersistence
from app.core.transcription.buffer import TranscriptBuffer
from app.core.logger import setup_logger

from app.api.routers.transcription.pipeline import spawn_claim_task
from app.api.routers.transcription.schemas import (
    TranscriptionResponse,
    TranscriptSegmentResponse,
    ClaimResponse,
    ClaimVerdictResponse,
    WSTranscriptMessage,
    WSSessionStartedMessage,
    WSSessionEndedMessage,
    WSErrorMessage,
)

logger = setup_logger(__name__)
router = APIRouter(prefix="/vaults/{vault_id}", tags=["transcription"])

SETTINGS = get_settings()

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
    """Transcribe pre-recorded audio with speaker diarization and
    verify detected claims against the vault's documents."""
    await require_vault_member(vault_id, current_user, db)

    start = time.monotonic()

    _validate_audio_file(file)
    audio_data = await file.read()
    _validate_audio_size(audio_data)

    mimetype = file.content_type or "audio/wav"

    # 1. Transcribe
    transcriber = get_transcriber()
    try:
        transcript = await asyncio.wait_for(
            transcriber.transcribe_file(audio_data, mimetype),
            timeout=SETTINGS.API_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Transcription timed out",
        )

    segments_response = [
        TranscriptSegmentResponse(
            text=s.text, speaker=s.speaker, start=s.start,
            end=s.end, confidence=s.confidence,
        )
        for s in transcript.segments
    ]

    # 2. Detect + verify claims
    claims_response: list[ClaimResponse] = []
    verdicts_response: list[ClaimVerdictResponse] = []

    if SETTINGS.CLAIM.DETECTION_ENABLED and transcript.segments:
        from app.core.copilot import extract_statements, verify_statement

        try:
            statements = await asyncio.wait_for(
                extract_statements(transcript.segments),
                timeout=SETTINGS.API_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("Statement extraction timed out — returning transcript without claims")
            statements = []

        claims_response = [
            ClaimResponse(
                id=s.id, text=s.text, speaker=s.speaker,
                timestamp_start=s.timestamp_start, timestamp_end=s.timestamp_end,
                context=s.context,
            )
            for s in statements
        ]

        if statements:
            raw_verdicts = await asyncio.gather(*[
                asyncio.wait_for(
                    verify_statement(s, vault_id),
                    timeout=SETTINGS.API_TIMEOUT_S,
                )
                for s in statements
            ], return_exceptions=True)

            for v in raw_verdicts:
                if isinstance(v, BaseException):
                    logger.warning(f"Statement verification failed: {v}")
                    continue
                verdicts_response.append(
                    ClaimVerdictResponse(
                        claim_id=v.claim_id, claim_text=v.claim_text,
                        verdict=v.verdict, confidence=v.confidence,
                        explanation=v.explanation, evidence=v.evidence,
                    )
                )

    latency = int((time.monotonic() - start) * 1000)
    logger.info(
        f"Transcription complete: {transcript.speakers} speakers, "
        f"{len(transcript.segments)} segments, {len(claims_response)} claims, {latency}ms",
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

    Concurrent tasks: audio sender (main loop), receiver (DeepGram → client),
    flush timer (claim trigger), DB flush, heartbeat.
    """
    user = await authenticate_websocket(websocket, db)
    if not user:
        return

    try:
        await require_vault_member(vault_id, user, db)
    except HTTPException:
        await websocket.close(code=4003, reason="Not a member of this vault")
        return

    # Concurrent session limit
    async with get_db_session() as session_db:
        active = (
            await session_db.execute(
                select(func.count()).where(
                    TranscriptionSession.user_id == user.id,
                    TranscriptionSession.status == "recording",
                )
            )
        ).scalar() or 0
        if active >= SETTINGS.MAX_CONCURRENT_TRANSCRIPTION_SESSIONS:
            await websocket.close(code=4008, reason="Too many active sessions")
            return

    await websocket.accept()
    logger.info(f"Live transcription started: vault={vault_id}, user={user.id}")

    persistence = SessionPersistence(vault_id=vault_id, user_id=user.id)
    try:
        await persistence.create_session()
    except Exception as exc:
        logger.error(f"Failed to create transcription session: {exc}")

    try:
        msg = WSSessionStartedMessage(
            session_id=str(persistence.session_id) if persistence.session_id else "",
        )
        await websocket.send_json(msg.model_dump())
    except Exception:
        pass

    buffer = TranscriptBuffer(vault_id=vault_id)
    claim_tasks: set[asyncio.Task] = set()
    claim_semaphore = asyncio.Semaphore(SETTINGS.CLAIM.MAX_CONCURRENT_TASKS)
    stop_event = asyncio.Event()

    transcriber = get_transcriber()
    sample_rate = int(websocket.query_params.get("sample_rate", "16000"))
    channels = int(websocket.query_params.get("channels", "1"))

    session_failed = False
    try:
        async with transcriber.live_session(
            sample_rate=sample_rate, channels=channels,
        ) as live:

            # -- Receiver: DeepGram → buffer → client -----------------------
            async def _receiver_loop() -> None:
                try:
                    while True:
                        try:
                            segment = await asyncio.wait_for(live.receive(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        except asyncio.CancelledError:
                            return
                        except Exception as exc:
                            logger.error(f"Unexpected receiver error: {exc}")
                            break

                        if segment is None:
                            if stop_event.is_set():
                                await asyncio.sleep(0.05)
                            continue

                        buffer.add_segment(segment)
                        persistence.buffer_segment(segment)

                        try:
                            msg = WSTranscriptMessage(
                                text=segment.text, speaker=segment.speaker,
                                start=segment.start, end=segment.end,
                                confidence=segment.confidence, is_final=segment.is_final,
                            )
                            await websocket.send_json(msg.model_dump())
                        except Exception:
                            stop_event.set()
                            return
                except asyncio.CancelledError:
                    pass

            # -- Flush timer: sole claim trigger ----------------------------
            async def _flush_timer_loop() -> None:
                while not stop_event.is_set():
                    await asyncio.sleep(SETTINGS.CLAIM.FLUSH_INTERVAL_S)
                    if stop_event.is_set():
                        break
                    if SETTINGS.CLAIM.DETECTION_ENABLED and buffer.should_trigger_claims():
                        spawn_claim_task(
                            websocket, buffer, persistence, vault_id,
                            claim_tasks, claim_semaphore,
                        )

            # -- DB flush: periodic segment persistence ---------------------
            async def _db_flush_loop() -> None:
                while not stop_event.is_set():
                    await asyncio.sleep(SETTINGS.TRANSCRIPTION.DB_FLUSH_INTERVAL_S)
                    if stop_event.is_set():
                        break
                    await persistence.flush_segments()

            # -- Heartbeat: keep WS alive through proxies/LBs --------------
            async def _heartbeat_loop() -> None:
                while not stop_event.is_set():
                    await asyncio.sleep(SETTINGS.WS_HEARTBEAT_INTERVAL_S)
                    if stop_event.is_set():
                        break
                    try:
                        await websocket.send_json({"type": "ping"})
                    except Exception:
                        stop_event.set()

            receiver_task = asyncio.create_task(_receiver_loop())
            flush_timer_task = asyncio.create_task(_flush_timer_loop())
            db_flush_task = asyncio.create_task(_db_flush_loop())
            heartbeat_task = asyncio.create_task(_heartbeat_loop())

            # -- Main loop: forward audio from client to DeepGram -----------
            try:
                while not stop_event.is_set():
                    try:
                        data = await asyncio.wait_for(
                            websocket.receive(),
                            timeout=SETTINGS.WS_RECEIVE_TIMEOUT_S,
                        )
                    except asyncio.TimeoutError:
                        logger.info(f"WS idle timeout ({SETTINGS.WS_RECEIVE_TIMEOUT_S}s): vault={vault_id}")
                        break
                    if "bytes" in data and data["bytes"]:
                        await live.send_audio(data["bytes"])
                    elif "text" in data and data["text"]:
                        try:
                            msg_data = json_mod.loads(data["text"])
                            if msg_data.get("type") == "stop":
                                break
                        except (json_mod.JSONDecodeError, TypeError):
                            pass

            except WebSocketDisconnect:
                logger.info(f"Client disconnected: vault={vault_id}")
            except Exception as e:
                logger.error(f"Live transcription error: {e}")
            finally:
                stop_event.set()

                # Session end drain
                await live.finalize()

                try:
                    await asyncio.wait_for(
                        receiver_task, timeout=SETTINGS.CLAIM.DRAIN_TIMEOUT_S,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    receiver_task.cancel()
                    try:
                        await receiver_task
                    except asyncio.CancelledError:
                        pass

                for t in (flush_timer_task, db_flush_task, heartbeat_task):
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

                await persistence.flush_segments()

                if SETTINGS.CLAIM.DETECTION_ENABLED and buffer.has_unchecked():
                    spawn_claim_task(
                        websocket, buffer, persistence, vault_id,
                        claim_tasks, claim_semaphore,
                    )

    except Exception as e:
        session_failed = True
        logger.error(f"Failed to start live transcription: {e}")
        await persistence.fail_session()
        try:
            await websocket.send_json(WSErrorMessage(message=str(e)).model_dump())
        except Exception:
            pass

    if not session_failed:
        duration = time.monotonic() - persistence._started_at
        try:
            msg = WSSessionEndedMessage(
                session_id=str(persistence.session_id) if persistence.session_id else "",
                duration_seconds=round(duration, 2),
            )
            await websocket.send_json(msg.model_dump())
        except Exception:
            pass

    try:
        await websocket.close(code=1000)
    except Exception:
        pass

    if claim_tasks:
        await asyncio.wait(claim_tasks, timeout=SETTINGS.CLAIM.TASK_TIMEOUT_S)

    if not session_failed:
        try:
            await persistence.finalize_session()
        except Exception as exc:
            logger.error(f"Failed to finalize session: {exc}")

    logger.info(
        f"Live transcription ended: vault={vault_id}, "
        f"{len(buffer.segments)} segments, {buffer.claims_detected} claims",
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_audio_file(file: UploadFile) -> None:
    """Validate the uploaded audio file type."""
    if file.content_type and file.content_type not in _AUDIO_MIMETYPES:
        filename = file.filename or ""
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in _AUDIO_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported audio format: {file.content_type}. "
                       f"Supported: wav, mp3, ogg, webm, flac, m4a, aac",
            )


def _validate_audio_size(audio_data: bytes) -> None:
    """Validate the audio file is non-empty and within size limits."""
    if len(audio_data) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio file is empty",
        )
    size_mb = len(audio_data) / (1024 * 1024)
    if size_mb > SETTINGS.TRANSCRIPTION.MAX_AUDIO_SIZE_MB:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio file too large: {size_mb:.1f}MB. "
                   f"Maximum: {SETTINGS.TRANSCRIPTION.MAX_AUDIO_SIZE_MB}MB",
        )
