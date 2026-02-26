"""Query agent — LangGraph ReAct agent for copilot chat.

Implements a tool-calling agent that uses ``search_documents``,
``lookup_entity``, ``filter_documents``, ``get_full_document``, and
``compute`` tools to answer user questions grounded in vault documents.

The agent follows an enhanced ReAct pattern with safety guardrails:
  1. Receive user query
  2. Plan: classify query type and inject strategy guidance
  3. Agent decides which tool(s) to call
  4. Guard rails: enforce iteration limit and context budget
  5. Observe tool results
  6. Either call more tools or produce a final answer

Key improvements over a bare ReAct loop:
  - **Query planning**: classifies aggregate vs point vs compute queries
    and injects optimal tool-selection guidance.
  - **Iteration limit**: enforced via ``AGENT_MAX_ITERATIONS`` — prevents
    infinite tool-calling loops.
  - **get_full_document budget**: max N calls per query, preventing
    context explosion on large corpora.
  - **Graceful tool errors**: ToolNode failures are caught and returned
    as error messages instead of crashing the graph.

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
    ToolMessage,
)
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app.core.copilot.tools import COPILOT_TOOLS
from app.core.copilot.prompts import AGENT_SYSTEM
from app.core.copilot.base import CopilotAnswer, Evidence
from app.core.rag.query import rewrite_query, extract_entity_ids
from app.core.copilot.classification import classify_query_type
from app.core.config import get_settings
from app.core.logger import setup_logger

logger = setup_logger(__name__)

SETTINGS = get_settings()


# ---------------------------------------------------------------------------
# Planning hints — injected before the user query based on classification
# ---------------------------------------------------------------------------

_PLANNING_HINTS = {
    "aggregate": (
        "[AGENT PLANNING NOTE: This is an AGGREGATE query — the user wants ALL matching documents. "
        "Use filter_documents to get the complete list via structured SQL filtering. "
        "Do NOT use search_documents for this — it only returns top-K results and will miss documents. "
        "filter_documents returns all matching documents with their key metadata.]"
    ),
    "compute": (
        "[AGENT PLANNING NOTE: This is a COMPUTATION query — the user wants a calculated total/sum/average. "
        "First use filter_documents to get all matching documents and their prices/data. "
        "Then use compute to calculate the result from the filter_documents output. "
        "Do NOT use search_documents for aggregate calculations.]"
    ),
    "point": (
        "[AGENT PLANNING NOTE: This is a POINT query about specific entities. "
        "Use lookup_entity for specific IDs, or search_documents for general questions. "
        "Use get_full_document only if you need complete content from a specific document.]"
    ),
}


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """State for the query agent graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    iteration_count: int
    full_doc_calls: int


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


def guard_rails(state: AgentState) -> dict:
    """Enforce iteration limits and budget constraints before tool execution.

    Checks:
      1. Total tool-call iterations vs ``AGENT_MAX_ITERATIONS``.
      2. Number of ``get_full_document`` calls vs ``AGENT_MAX_FULL_DOC_CALLS``.

    When limits are exceeded, replaces tool-call messages with a warning
    message that forces the agent to produce a final answer with the
    information it already has.
    """
    iteration = state.get("iteration_count", 0) + 1
    full_doc_calls = state.get("full_doc_calls", 0)
    max_iterations = SETTINGS.COPILOT.AGENT_MAX_ITERATIONS
    max_full_doc = SETTINGS.COPILOT.AGENT_MAX_FULL_DOC_CALLS

    # Count get_full_document calls in the latest AI message
    last_msg = state["messages"][-1] if state["messages"] else None
    new_full_doc = 0
    if last_msg and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        for tc in last_msg.tool_calls:
            if tc.get("name") == "get_full_document":
                new_full_doc += 1

    updated_full_doc = full_doc_calls + new_full_doc

    # Check iteration limit
    if iteration > max_iterations:
        logger.warning(
            f"Agent hit iteration limit ({max_iterations}). "
            f"Forcing final answer."
        )
        return {
            "iteration_count": iteration,
            "full_doc_calls": updated_full_doc,
            "messages": [
                AIMessage(content=(
                    "I've gathered enough information from the searches above. "
                    "Let me compile a comprehensive answer based on what I found."
                )),
            ],
        }

    # Check full-doc budget (block new get_full_document calls)
    if new_full_doc > 0 and updated_full_doc > max_full_doc:
        logger.warning(
            f"Agent exceeded get_full_document budget ({max_full_doc}). "
            f"Blocking call, using existing data."
        )
        # Create a tool message for each blocked call that explains the limit
        blocked_messages: list[AnyMessage] = []
        if last_msg and hasattr(last_msg, "tool_calls"):
            for tc in last_msg.tool_calls:
                if tc.get("name") == "get_full_document":
                    blocked_messages.append(ToolMessage(
                        content=(
                            "get_full_document budget exceeded for this query. "
                            "Please answer using the data already retrieved."
                        ),
                        tool_call_id=tc.get("id", ""),
                    ))
        return {
            "iteration_count": iteration,
            "full_doc_calls": updated_full_doc,
            "messages": blocked_messages,
        }

    return {
        "iteration_count": iteration,
        "full_doc_calls": updated_full_doc,
    }


def should_continue(state: AgentState) -> str:
    """Route after agent: tools if tool calls pending, else end.

    Also checks iteration limit — if exceeded, forces END even if
    the agent wants to call more tools.
    """
    iteration = state.get("iteration_count", 0)
    max_iterations = SETTINGS.COPILOT.AGENT_MAX_ITERATIONS

    # If we already hit the limit and guard_rails injected a final-answer message
    if iteration > max_iterations:
        return END

    # Standard tools_condition logic
    last_msg = state["messages"][-1] if state["messages"] else None
    if last_msg and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "guard_rails"
    return END


# ---------------------------------------------------------------------------
# Safe ToolNode — catches tool execution errors gracefully
# ---------------------------------------------------------------------------

class SafeToolNode(ToolNode):
    """ToolNode that catches exceptions and returns error messages.

    Prevents the entire agent graph from crashing when a single tool
    call fails (e.g. DB timeout, embedding API error).
    """

    async def _arun(self, input, config=None, **kwargs):
        try:
            return await super()._arun(input, config=config, **kwargs)
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            # Return error messages for each tool call
            last_msg = input.get("messages", [])[-1] if input.get("messages") else None
            error_messages = []
            if last_msg and hasattr(last_msg, "tool_calls"):
                for tc in last_msg.tool_calls:
                    error_messages.append(ToolMessage(
                        content=f"Tool error: {e}. Try a different approach.",
                        tool_call_id=tc.get("id", ""),
                    ))
            return {"messages": error_messages} if error_messages else {}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_agent_graph() -> StateGraph:
    """Build the enhanced ReAct query agent graph with guardrails.

    Flow::

        START → agent → [should_continue]
            → has tool calls: guard_rails → [budget check]
                → within budget: tools → agent → ...
                → over budget: agent (with warning) → ...
            → no tool calls: END
    """
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("guard_rails", guard_rails)
    graph.add_node("tools", SafeToolNode(COPILOT_TOOLS))

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"guard_rails": "guard_rails", END: END})
    graph.add_edge("guard_rails", "tools")
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
# Message builder — shared by both invocation and streaming
# ---------------------------------------------------------------------------

def _build_messages(
    rewritten_query: str,
    history: list[dict[str, str]] | None = None,
) -> list[AnyMessage]:
    """Build the message list for the agent including system prompt and planning hint.

    Combines:
      1. System prompt
      2. Conversation history (truncated)
      3. Planning hint based on query classification
      4. User's rewritten query
    """
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

    # Classify and inject planning hint
    query_type = classify_query_type(rewritten_query)
    hint = _PLANNING_HINTS.get(query_type, "")
    if hint:
        messages.append(SystemMessage(content=hint))

    messages.append(HumanMessage(content=rewritten_query))

    logger.info(f"Agent query classified as '{query_type}': '{rewritten_query[:60]}'")
    return messages


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
    2. Classifies query type and builds messages with planning hint.
    3. Invokes the LangGraph agent graph with guardrails.
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

    # 2. Build messages with planning hint
    messages = _build_messages(rewritten, history)

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
            graph.ainvoke(
                {"messages": messages, "iteration_count": 0, "full_doc_calls": 0},
                config=config,
            ),
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

    # 2. Build messages with planning hint
    messages = _build_messages(rewritten, history)

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
            {"messages": messages, "iteration_count": 0, "full_doc_calls": 0},
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
