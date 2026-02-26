"""Query/statement classification — shared across chat and verification.

Centralizes aggregate vs point detection so both paths stay aligned.
Also provides aggregate intent hints (count/sum/avg/list) for fast paths.

Classification uses a two-tier point-override to prevent false-positive
aggregate routing for specific-entity queries:

  1. **Strong override** (``this order``, ``that invoice``): always point,
     even when other aggregate signals are present.
  2. **Weak override** (``the order``, proper name after preposition):
     point only when no explicit multi-document quantifier
     (``all``, ``every``, ``each``, ``how many``) is present.

This prevents statements like "the total price of the order for Yang Wang"
from being routed to the aggregate fast-path (which requires SQL-
parseable filters and will reject anything it cannot parse).
"""

from __future__ import annotations

import re
from typing import Literal

from app.core.utils import normalize_numbers


AggregateType = Literal["point", "aggregate", "compute"]
AggregateIntent = Literal["count", "sum", "average", "list"]


# ---------------------------------------------------------------------------
# Aggregate detection
# ---------------------------------------------------------------------------

_AGGREGATE_PATTERNS = [
    r"\ball\b.*\b(?:invoice|order|report|document|item|product|shipping|purchase)",
    # "total price/cost of ALL ..." — requires explicit aggregate qualifier.
    # Previously matched bare "for"/"in" which false-positived on
    # "total price for Yang Wang" (a point query about one entity).
    r"\btotal\b.*\b(?:price|cost|amount|value|number|count|quantity|items)\b.*\b(?:of all|for all|from all|across)\b",
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
    # Entity-type + temporal context (month or year)
    r"\b(?:invoices?|orders?|reports?|documents?|items?|products?)\b.*\b(?:from|in|of|during|for)\b.*\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december|20\d{2}|q[1-4])\b",
    # "total price/cost from July 2016" — value-word + temporal context
    r"\btotal\b.*\b(?:price|cost|amount|value)\b.*\b(?:from|in|during|for)\b.*\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december|20\d{2}|q[1-4])\b",
]

_AGGREGATE_RE = re.compile("|".join(_AGGREGATE_PATTERNS), re.IGNORECASE)

_COMPUTE_RE = re.compile(
    r"\b(?:total|sum|average|avg|calculate|compute|add up|combined)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Point-override detection (prevents aggregate false-positives)
# ---------------------------------------------------------------------------

# Strong: demonstrative + singular entity noun → always point.
# "this order", "that invoice" — unambiguously references ONE entity.
_STRONG_POINT_RE = re.compile(
    r"\b(?:this|that)\s+(?:order|invoice|document|report|item|product|shipment)\b",
    re.IGNORECASE,
)

# Weak: definite article + singular entity noun → point only when no
# explicit multi-document quantifier is present.
# "the order" is specific, but "all the orders" is aggregate.
_WEAK_POINT_RE = re.compile(
    r"\bthe\s+(?:order|invoice|document|report|item|product|shipment)\b",
    re.IGNORECASE,
)

# Proper noun after preposition — applied to ORIGINAL text (case-sensitive).
# Matches "for Yang Wang", "about John Smith", etc.
_PROPER_NAME_AFTER_PREP_RE = re.compile(
    r"\b(?:for|about|by)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+",
)

# Multi-document quantifiers — unambiguous signals that the query
# operates over a SET of documents, not a single entity.
_MULTI_DOC_SIGNAL_RE = re.compile(
    r"\b(?:all|every|each|how many)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Aggregate intent (count / sum / average / list)
# ---------------------------------------------------------------------------

_COUNT_RE = re.compile(
    r"\b(?:count|how many|number of|total number)\b"
    r"|\btotal\s+(?:invoices?|orders?|documents?|items?|products?|reports?)\b",
    re.IGNORECASE,
)

_AVERAGE_RE = re.compile(r"\b(?:average|avg)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_query_type(text: str) -> AggregateType:
    """Classify a query/statement as aggregate, compute, or point.

    Uses a two-tier point-override to prevent false aggregate routing:

      1. Strong override: "this order" — always point, even with "all".
      2. Weak override: "the order", proper names — point only when no
         explicit multi-doc quantifier (all/every/each/how many) found.

    Args:
        text: Raw statement text (original casing preserved for name detection).

    Returns:
        "aggregate", "compute", or "point".
    """
    normalized = normalize_numbers(text.lower())

    if not _AGGREGATE_RE.search(normalized):
        return "point"

    # --- Aggregate pattern matched — check for point overrides ---

    # Strong: demonstrative reference always wins
    if _STRONG_POINT_RE.search(normalized):
        return "point"

    # Weak: specific-entity reference wins only without multi-doc quantifier
    if not _MULTI_DOC_SIGNAL_RE.search(normalized):
        if _WEAK_POINT_RE.search(normalized):
            return "point"
        # Case-sensitive proper-name check on ORIGINAL text
        if _PROPER_NAME_AFTER_PREP_RE.search(text):
            return "point"

    # Genuine aggregate
    if _COMPUTE_RE.search(normalized):
        return "compute"
    return "aggregate"


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
