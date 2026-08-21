from pydantic import BaseModel, field_validator

from app.core.rag.generation import Citation


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class HistoryMessage(BaseModel):
    """A single message in the conversation history."""

    role: str  # "user" or "assistant"
    content: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        return v

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        # Allow empty for assistant placeholders, but trim
        return v.strip()


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    history: list[HistoryMessage] = []

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Query cannot be empty")
        if len(v) > 2000:
            raise ValueError("Query must be at most 2000 characters")
        return v

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v < 1 or v > 20:
            raise ValueError("top_k must be between 1 and 20")
        return v

    @field_validator("history")
    @classmethod
    def validate_history(cls, v: list) -> list:
        # Limit history size to prevent abuse (max 50 messages)
        if len(v) > 50:
            v = v[-50:]
        return v


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
    confidence: float = 0.0
    has_sufficient_evidence: bool = False
    chunks_used: int = 0
    retrieval_method: str = "hybrid"
    latency_ms: int = 0
