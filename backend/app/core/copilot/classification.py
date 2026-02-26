"""Query/statement classification — shared across chat and verification.

Centralizes aggregate vs point detection so both paths stay aligned.
Also provides aggregate intent hints (count/sum/avg/list) for fast paths.
"""

from __future__ import annotations

import re
from typing import Literal

from app.core.utils import normalize_numbers


AggregateType = Literal["point", "aggregate", "compute"]
AggregateIntent = Literal["count", "sum", "average", "list"]


# Aggregate query patterns — union of chat + verification patterns
_AGGREGATE_PATTERNS = [
    r"\ball\b.*\b(?:invoice|order|report|document|item|product|shipping|purchase)",
    r"\btotal\b.*\b(?:price|cost|amount|value|number|count|quantity|items)\b.*\b(?:of all|from|in|for|during)\b",
    r"\bhow many\b",
    r"\bcount\b.*\b(?:of|all|the)\b",
    r"\btotal number\b",
    r"\bnumber of\b",
    r"\blist\b.*\b(?:every|all|each)\b",
    r"\bevery\b",
    r"\btypes of documents\b",
    r"\bwhat.*(?:do we have|exist|available)\b",
    r"\b(?:stock|inventory)\b.*\breport",
    r"\b(?:exist|available)\b.*\b(?:in the vault|in the database)\b",
    r"\b(?:invoices?|orders?|reports?|documents?|items?|products?)\b.*\b(?:from|in|of|during|for)\b.*\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december|20\d{2}|q[1-4])\b",
]

_AGGREGATE_RE = re.compile("|".join(_AGGREGATE_PATTERNS), re.IGNORECASE)

_COMPUTE_RE = re.compile(
    r"\b(?:total|sum|average|avg|calculate|compute|add up|combined)\b",
    re.IGNORECASE,
)

_COUNT_RE = re.compile(
    r"\b(?:count|how many|number of|total number)\b"
    r"|\btotal\s+(?:invoices?|orders?|documents?|items?|products?|reports?)\b",
    re.IGNORECASE,
)

_AVERAGE_RE = re.compile(r"\b(?:average|avg)\b", re.IGNORECASE)


def classify_query_type(text: str) -> AggregateType:
    """Classify a query/statement as aggregate, compute, or point."""
    normalized = normalize_numbers(text.lower())
    if _AGGREGATE_RE.search(normalized):
        if _COMPUTE_RE.search(normalized):
            return "compute"
        return "aggregate"
    return "point"


def is_aggregate_query(text: str) -> bool:
    """Return True for aggregate or compute queries."""
    return classify_query_type(text) in ("aggregate", "compute")


def infer_aggregate_intent(text: str) -> AggregateIntent:
    """Infer the aggregate operation to perform."""
    normalized = normalize_numbers(text.lower())
    if _COUNT_RE.search(normalized):
        return "count"
    if _AVERAGE_RE.search(normalized):
        return "average"
    if _COMPUTE_RE.search(normalized):
        return "sum"
    return "list"
