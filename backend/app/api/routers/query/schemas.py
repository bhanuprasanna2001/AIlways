from pydantic import BaseModel, field_validator

from app.core.rag.reasoning.schemas import Citation


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5

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


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
    confidence: float = 0.0
    has_sufficient_evidence: bool = False
    chunks_used: int = 0
