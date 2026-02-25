"""Query agent — LangGraph ReAct agent for copilot chat.

Implements a tool-calling agent that uses ``search_documents`` and
``lookup_entity`` tools to answer user questions grounded in vault
documents.

The agent follows the ReAct pattern:
  1. Receive user query
  2. Decide which tool(s) to call
  3. Observe tool results
  4. Either call more tools or produce a final answer

This replaces the linear query pipeline (rewrite → embed → search →
generate) with an agentic loop that can do multi-hop retrieval,
choose between search strategies, and self-correct.

For streaming, the agent graph yields token-by-token deltas that
the SSE endpoint can forward to the frontend.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, TypedDict
from uuid import UUID

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    SystemMessage,
    AIMessage,
)
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app.core.copilot.tools import COPILOT_TOOLS
from app.core.copilot.prompts import AGENT_SYSTEM
from app.core.copilot.base import CopilotAnswer, Evidence
from app.core.rag.query import rewrite_query, extract_entity_ids
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """State for the query agent graph."""

    messages: Annotated[list[AnyMessage], add_messages]


# ---------------------------------------------------------------------------
# Agent LLM (lazy singleton)
# ---------------------------------------------------------------------------

_agent_llm: ChatOpenAI | None = None


def _get_agent_llm() -> ChatOpenAI:
    global _agent_llm
    if _agent_llm is None:
        model = SETTINGS.COPILOT.AGENT_MODEL or SETTINGS.OPENAI_REASONING_MODEL
        _agent_llm = ChatOpenAI(
            model=model,
            temperature=SETTINGS.COPILOT.AGENT_TEMPERATURE,
            api_key=SETTINGS.OPENAI_API_KEY,
        ).bind_tools(COPILOT_TOOLS)
    return _agent_llm


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

async def agent_node(state: AgentState) -> dict:
    """Call the LLM with tool bindings.

    The LLM decides whether to call a tool or produce a final response.
    LangGraph's ``tools_condition`` routes accordingly.
    """
    llm = _get_agent_llm()
    response = await llm.ainvoke(state["messages"])
    return {"messages": [response]}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_agent_graph() -> StateGraph:
    """Build the ReAct query agent graph.

    Flow::

        START → agent → [tools_condition]
            → has tool calls: tool_node → agent → ...
            → no tool calls: END
    """
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(COPILOT_TOOLS))

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")

    return graph


# Module-level compiled graph (lazy singleton)
_compiled_graph = None


def get_agent_graph():
    """Return the compiled agent graph (lazily built)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_agent_graph().compile()
    return _compiled_graph


# ---------------------------------------------------------------------------
# Public API — full invocation
# ---------------------------------------------------------------------------

async def query_vault_agent(
    query: str,
    vault_id: UUID,
    history: list[dict[str, str]] | None = None,
    top_k: int | None = None,
) -> CopilotAnswer:
    """Run the query agent and return a structured answer.

    1. Rewrites the query for coreference resolution.
    2. Builds message history for the agent.
    3. Invokes the LangGraph agent graph.
    4. Parses the final response into ``CopilotAnswer``.

    Args:
        query: The user's current question.
        vault_id: Vault to search against.
        history: Optional prior conversation ``[{"role": ..., "content": ...}]``.
        top_k: Override default ``RAG_SEARCH_TOP_K``.

    Returns:
        CopilotAnswer with answer text, citations, and confidence.
    """
    # 1. Rewrite query for standalone form
    rewritten = await rewrite_query(query, history)

    # 2. Build messages
    messages: list[AnyMessage] = [SystemMessage(content=AGENT_SYSTEM)]

    if history:
        for msg in history[-SETTINGS.QUERY_HISTORY_MAX_TURNS * 2:]:
            role = msg.get("role", "user")
            content = msg.get("content", "").strip()
            if not content:
                continue
            if role == "assistant":
                # Truncate long prior answers
                if len(content) > 500:
                    content = content[:500] + "..."
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))

    messages.append(HumanMessage(content=rewritten))

    # 3. Invoke agent
    graph = get_agent_graph()
    config = {
        "configurable": {
            "vault_id": vault_id,
            "top_k": top_k or SETTINGS.RAG_SEARCH_TOP_K,
        }
    }

    try:
        result = await asyncio.wait_for(
            graph.ainvoke({"messages": messages}, config=config),
            timeout=SETTINGS.API_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error("Query agent timed out")
        return CopilotAnswer(answer="Query timed out. Please try again.")
    except Exception as e:
        logger.error(f"Query agent failed: {e}")
        return CopilotAnswer(answer=f"An error occurred: {e}")

    # 4. Extract final answer from last AI message
    final_messages = result.get("messages", [])
    answer_text = ""
    for msg in reversed(final_messages):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            answer_text = msg.content
            break

    if not answer_text:
        return CopilotAnswer(answer="I couldn't find relevant information in the vault.")

    return CopilotAnswer(
        answer=answer_text,
        confidence=0.8,
        has_sufficient_evidence=True,
    )


# ---------------------------------------------------------------------------
# Public API — streaming
# ---------------------------------------------------------------------------

async def stream_vault_agent(
    query: str,
    vault_id: UUID,
    history: list[dict[str, str]] | None = None,
    top_k: int | None = None,
) -> AsyncIterator[dict]:
    """Stream the query agent's response as events.

    Yields dicts with event types compatible with the existing SSE
    protocol:
      - ``{"type": "retrieval", "chunks_used": N}`` (when tools return)
      - ``{"type": "token", "content": "..."}`` (LLM content deltas)
      - ``{"type": "done", ...}`` (final structured response)
      - ``{"type": "error", ...}`` (on failure)

    Args:
        query: The user's current question.
        vault_id: Vault to search against.
        history: Optional prior conversation.
        top_k: Override default top_k.
    """
    # 1. Rewrite
    rewritten = await rewrite_query(query, history)

    # 2. Build messages
    messages: list[AnyMessage] = [SystemMessage(content=AGENT_SYSTEM)]

    if history:
        for msg in history[-SETTINGS.QUERY_HISTORY_MAX_TURNS * 2:]:
            role = msg.get("role", "user")
            content = msg.get("content", "").strip()
            if not content:
                continue
            if role == "assistant":
                if len(content) > 500:
                    content = content[:500] + "..."
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))

    messages.append(HumanMessage(content=rewritten))

    # 3. Stream agent
    graph = get_agent_graph()
    config = {
        "configurable": {
            "vault_id": vault_id,
            "top_k": top_k or SETTINGS.RAG_SEARCH_TOP_K,
        }
    }

    tool_call_count = 0
    full_content = ""

    try:
        async for event in graph.astream_events(
            {"messages": messages},
            config=config,
            version="v2",
        ):
            kind = event.get("event", "")

            # Tool invocations — emit retrieval event
            if kind == "on_tool_end":
                tool_call_count += 1
                yield {"type": "retrieval", "chunks_used": tool_call_count}

            # LLM token deltas — only from the final response (not tool calls)
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    # Only yield content when no tool calls are pending
                    if not (hasattr(chunk, "tool_calls") and chunk.tool_calls):
                        full_content += chunk.content
                        yield {"type": "token", "content": chunk.content}

        # Final structured event
        yield {
            "type": "done",
            "answer": full_content or "I couldn't find relevant information.",
            "citations": [],
            "confidence": 0.8 if full_content else 0.0,
            "has_sufficient_evidence": bool(full_content),
            "chunks_used": tool_call_count,
            "retrieval_method": "agent",
        }

    except Exception as e:
        logger.error(f"Stream agent failed: {e}")
        yield {
            "type": "error",
            "answer": f"An error occurred: {e}",
            "confidence": 0.0,
        }
