from enum import StrEnum

from pydantic import BaseModel

from app.core.rag.reasoning.schemas import Citation


class QueryType(StrEnum):
    """Classification of query intent for retrieval strategy routing."""

    FACTUAL_LOOKUP = "factual_lookup"
    TEMPORAL_CLAIM = "temporal_claim"
    COMPARISON = "comparison"
    AGGREGATION = "aggregation"
    EXISTENCE_CHECK = "existence_check"
    VAGUE_EXPLORATORY = "vague_exploratory"


class ClassificationResult(BaseModel):
    """Output of the query classifier."""

    query_type: QueryType = QueryType.FACTUAL_LOOKUP
    is_multi_part: bool = False
    entities: list[str] = []
    temporal_refs: list[str] = []
    confidence: float = 0.5


class RetrievalQuality(StrEnum):
    """Retrieval quality tier for CRAG decision."""

    HIGH = "high"
    UNCERTAIN = "uncertain"
    INSUFFICIENT = "insufficient"


class RetrievalQualitySignals(BaseModel):
    """Heuristic signals evaluating retrieval quality without an LLM call."""

    quality: RetrievalQuality = RetrievalQuality.INSUFFICIENT
    score: float = 0.0
    top_score: float = 0.0
    score_spread: float = 0.0
    entity_overlap_ratio: float = 0.0
    result_count: int = 0
    corrective_action: str | None = None


class QueryPipelineResult(BaseModel):
    """Full output of the query intelligence pipeline."""

    answer: str
    citations: list[Citation] = []
    confidence: float = 0.0
    has_sufficient_evidence: bool = False
    query_type: str = ""
    quality_score: float = 0.0
    quality_signals: RetrievalQualitySignals | None = None
    corrective_action_taken: str | None = None
    queries_used: list[str] = []
    chunks_used: int = 0
    rewritten_query: str | None = None
    retrieval_method: str = "hybrid"
    latency_ms: int = 0
