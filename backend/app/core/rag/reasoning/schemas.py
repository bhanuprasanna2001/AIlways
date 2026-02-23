from pydantic import BaseModel


class Citation(BaseModel):
    """A citation pointing back to source content."""
    doc_title: str
    section: str | None = None
    page: int | None = None
    quote: str


class ReasoningResult(BaseModel):
    """The output of the reasoning engine."""
    answer: str
    citations: list[Citation] = []
    confidence: float = 0.0
    has_sufficient_evidence: bool = False
