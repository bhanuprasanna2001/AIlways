"""Claim processing pipeline — detection and concurrent verification.

Extracted from the live transcription WebSocket handler. Handles
the detect → deduplicate → persist → verify → notify lifecycle
for a captured batch of transcript segments.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import WebSocket

from app.core.claims import get_claim_detector, get_claim_verifier
from app.core.claims.base import Claim
from app.core.transcription.base import TranscriptSegment
from app.core.transcription.buffer import TranscriptBuffer
from app.core.transcription.persistence import SessionPersistence
from app.core.config import get_settings
from app.core.logger import setup_logger
from app.db import get_db_session

from app.api.routers.transcription.schemas import (
    WSClaimDetectedMessage,
    WSClaimVerifiedMessage,
)

logger = setup_logger(__name__)

SETTINGS = get_settings()


def spawn_claim_task(
    websocket: WebSocket,
    buffer: TranscriptBuffer,
    persistence: SessionPersistence,
    vault_id: UUID,
    claim_tasks: set[asyncio.Task],
    claim_semaphore: asyncio.Semaphore,
) -> None:
    """Capture unchecked segments and create a background claim task.

    The batch is captured synchronously so the pointer advances
    immediately, preventing duplicate processing on the next timer tick.
    """
    context_segments, unchecked = buffer.get_claim_batch()
    if not unchecked:
        return

    entity_summary = buffer.entity_summary

    async def _guarded_process() -> None:
        async with claim_semaphore:
            await process_claims_batch(
                websocket, buffer, persistence, vault_id,
                context_segments, unchecked, entity_summary,
            )

    task = asyncio.create_task(_guarded_process())
    claim_tasks.add(task)
    task.add_done_callback(claim_tasks.discard)


async def process_claims_batch(
    websocket: WebSocket,
    buffer: TranscriptBuffer,
    persistence: SessionPersistence,
    vault_id: UUID,
    context_segments: list[TranscriptSegment],
    unchecked: list[TranscriptSegment],
    entity_summary: str,
) -> None:
    """Detect claims from a captured batch and verify them concurrently."""
    try:
        detector = get_claim_detector()
        claims = await asyncio.wait_for(
            detector.detect_claims(
                segments=unchecked,
                context_segments=context_segments or None,
                entity_summary=entity_summary,
            ),
            timeout=SETTINGS.API_TIMEOUT_S,
        )

        new_claims = buffer.deduplicate_claims(claims)
        buffer.claims_detected += len(new_claims)

        all_text = " ".join(s.text for s in (context_segments or []) + unchecked)
        buffer.update_entities(all_text)

        if not new_claims:
            return

        # Persist + notify all detected claims (fast, no external API calls).
        # WS sends are best-effort — if closed, we still persist and verify.
        ws_alive = True
        claim_db_ids: dict[str, UUID] = {}
        for claim in new_claims:
            try:
                db_id = await persistence.persist_claim(claim)
                claim_db_ids[claim.id] = db_id
            except Exception as exc:
                logger.error(f"Failed to persist claim '{claim.text[:50]}': {exc}")

            if ws_alive:
                try:
                    msg = WSClaimDetectedMessage(
                        claim_id=claim.id, text=claim.text, speaker=claim.speaker,
                    )
                    await websocket.send_json(msg.model_dump())
                except Exception:
                    ws_alive = False

        # Verify all claims concurrently (each with its own DB session)
        verifier = get_claim_verifier()

        async def _verify_one(claim: Claim):
            try:
                async with get_db_session() as db:
                    return await asyncio.wait_for(
                        verifier.verify_claim(claim, vault_id, db),
                        timeout=SETTINGS.API_TIMEOUT_S,
                    )
            except asyncio.TimeoutError:
                logger.warning(f"Verification timed out for '{claim.text[:50]}'")
                return None
            except Exception as exc:
                logger.error(f"Verification failed for '{claim.text[:50]}': {exc}")
                return None

        verdicts = await asyncio.gather(*[_verify_one(c) for c in new_claims])

        for claim, verdict in zip(new_claims, verdicts):
            if verdict is None:
                continue

            db_id = claim_db_ids.get(claim.id)
            if db_id:
                try:
                    evidence_dicts = [
                        e.model_dump() if hasattr(e, "model_dump") else e
                        for e in verdict.evidence
                    ] if verdict.evidence else None
                    await persistence.update_verdict(
                        claim_id=db_id,
                        verdict=verdict.verdict,
                        confidence=verdict.confidence,
                        explanation=verdict.explanation,
                        evidence=evidence_dicts,
                    )
                except Exception as exc:
                    logger.error(f"Failed to persist verdict for '{claim.text[:50]}': {exc}")

            if ws_alive:
                try:
                    msg = WSClaimVerifiedMessage(
                        claim_id=verdict.claim_id,
                        claim_text=verdict.claim_text,
                        verdict=verdict.verdict,
                        confidence=verdict.confidence,
                        explanation=verdict.explanation,
                        evidence=verdict.evidence,
                    )
                    await websocket.send_json(msg.model_dump())
                except Exception:
                    ws_alive = False

    except asyncio.TimeoutError:
        logger.warning("Claim detection timed out")
    except Exception as e:
        logger.error(f"Claim processing error: {e}")
