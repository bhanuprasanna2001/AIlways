"""Copilot package — agentic RAG with LangGraph.

Two main capabilities:
  1. **Statement extraction + verification** (real-time transcription):
     ``extract_statements`` → ``verify_statement`` (per-statement CRAG graph).
  2. **Query agent** (copilot chat):
     ``query_vault_agent`` / ``stream_vault_agent`` (ReAct agent with tools).

Usage::

    from app.core.copilot import extract_statements, verify_statement
    from app.core.copilot import query_vault_agent, stream_vault_agent

    # Transcription pipeline
    statements = await extract_statements(segments, context_segments)
    verdict = await verify_statement(statement, vault_id)

    # Copilot chat
    answer = await query_vault_agent(query, vault_id, history)
    async for event in stream_vault_agent(query, vault_id, history):
        ...
"""

from app.core.copilot.base import (
    Statement,
    Evidence,
    Verdict,
    CopilotAnswer,
)
from app.core.copilot.extraction import extract_statements
from app.core.copilot.verification import verify_statement
from app.core.copilot.agent import query_vault_agent, stream_vault_agent

__all__ = [
    "Statement",
    "Evidence",
    "Verdict",
    "CopilotAnswer",
    "extract_statements",
    "verify_statement",
    "query_vault_agent",
    "stream_vault_agent",
]
