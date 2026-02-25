"""Copilot-powered verification pipeline — extraction and concurrent verification.

Extracted from the live transcription WebSocket handler. Handles
the extract → deduplicate → persist → verify → notify lifecycle
for a captured batch of transcript segments.

Uses the LangGraph-based copilot module:
  - ``extract_statements`` for pulling verifiable facts from transcript
  - ``verify_statement`` for self-corrective retrieval + verdict synthesis
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import WebSocket

from app.core.copilot import extract_statements, verify_statement
from app.core.copilot.base import Statement
from app.core.transcription.base import TranscriptSegment
from app.core.transcription.buffer import TranscriptBuffer
from app.core.transcription.persistence import SessionPersistence
from app.core.config import get_settings
from app.core.logger import setup_logger

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
    """Capture unchecked segments and create a background verification task.

    The batch is captured synchronously so the pointer advances
    immediately, preventing duplicate processing on the next timer tick.
    """
    context_segments, unchecked = buffer.get_claim_batch()
    if not unchecked:
        return

    entity_summary = buffer.entity_summary

    async def _guarded_process() -> None:
        async with claim_semaphore:
            await process_statements_batch(
                websocket, buffer, persistence, vault_id,
                context_segments, unchecked, entity_summary,
            )

    task = asyncio.create_task(_guarded_process())
    claim_tasks.add(task)
    task.add_done_callback(claim_tasks.discard)


async def process_statements_batch(
    websocket: WebSocket,
    buffer: TranscriptBuffer,
    persistence: SessionPersistence,
    vault_id: UUID,
    context_segments: list[TranscriptSegment],
    unchecked: list[TranscriptSegment],
    entity_summary: str,
) -> None:
    """Extract statements from a captured batch and verify them with the LangGraph CRAG pipeline."""
    try:
        # 1. Extract verifiable statements (single Groq LLM call)
        statements = await asyncio.wait_for(
            extract_statements(
                segments=unchecked,
                context_segments=context_segments or None,
                entity_summary=entity_summary,
            ),
            timeout=SETTINGS.API_TIMEOUT_S,
        )

        # 2. Deduplicate against previously seen statements
        new_statements = buffer.deduplicate_claims(statements)
        buffer.claims_detected += len(new_statements)

        # 3. Update entity tracker
        all_text = " ".join(s.text for s in (context_segments or []) + unchecked)
        buffer.update_entities(all_text)

        if not new_statements:
            return

        # 4. Persist + notify all detected statements
        ws_alive = True
        statement_db_ids: dict[str, UUID] = {}
        for stmt in new_statements:
            try:
                db_id = await persistence.persist_claim(stmt)
                statement_db_ids[stmt.id] = db_id
            except Exception as exc:
                logger.error(f"Failed to persist statement '{stmt.text[:50]}': {exc}")

            if ws_alive:
                try:
                    msg = WSClaimDetectedMessage(
                        claim_id=stmt.id, text=stmt.text, speaker=stmt.speaker,
                    )
                    await websocket.send_json(msg.model_dump())
                except Exception:
                    ws_alive = False

        # 5. Verify all statements concurrently via LangGraph CRAG graph
        async def _verify_one(stmt: Statement):
            try:
                return await asyncio.wait_for(
                    verify_statement(stmt, vault_id),
                    timeout=SETTINGS.API_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Verification timed out for '{stmt.text[:50]}'")
                return None
            except Exception as exc:
                logger.error(f"Verification failed for '{stmt.text[:50]}': {exc}")
                return None

        verdicts = await asyncio.gather(*[_verify_one(s) for s in new_statements])

        # 6. Persist verdicts + notify via WebSocket
        for stmt, verdict in zip(new_statements, verdicts):
            if verdict is None:
                continue

            db_id = statement_db_ids.get(stmt.id)
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
                    logger.error(f"Failed to persist verdict for '{stmt.text[:50]}': {exc}")

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
        logger.warning("Statement extraction timed out")
    except Exception as e:
        logger.error(f"Statement processing error: {e}")
