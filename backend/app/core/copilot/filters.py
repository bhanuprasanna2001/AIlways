"""Shared query filter parsing for aggregate document queries."""

from __future__ import annotations

import re
from datetime import date


# Month name → numeric value for date parsing
_MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def parse_document_type(query: str) -> str | None:
    """Extract document type from a natural language query."""
    q = query.lower()
    if any(w in q for w in ("invoice", "invoices")):
        return "invoice"
    if any(w in q for w in ("purchase order", "purchase orders", "purchase_order")):
        return "purchase_order"
    if any(w in q for w in ("shipping order", "shipping orders", "shipment", "shipping_order")):
        return "shipping_order"
    if any(w in q for w in ("stock report", "stock reports", "inventory report", "stock_report")):
        return "stock_report"
    return None


def parse_date_range(query: str) -> tuple[date | None, date | None]:
    """Extract a date range from a natural language query.

    Handles:
      - "July 2016"   → (2016-07-01, 2016-07-31)
      - "2017"         → (2017-01-01, 2017-12-31)
      - "Q3 2016"      → (2016-07-01, 2016-09-30)
    """
    q = query.lower()

    # Extract year
    year_match = re.search(r"\b(20\d{2})\b", q)
    year = int(year_match.group(1)) if year_match else None

    if not year:
        return None, None

    # Check for quarter
    q_match = re.search(r"\bq([1-4])\b", q)
    if q_match:
        quarter = int(q_match.group(1))
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 2
        return (
            date(year, start_month, 1),
            _last_day_of_month(year, end_month),
        )

    # Check for month name
    for month_name, month_num in _MONTH_MAP.items():
        if month_name in q:
            return (
                date(year, month_num, 1),
                _last_day_of_month(year, month_num),
            )

    # Year only
    return date(year, 1, 1), date(year, 12, 31)


def _last_day_of_month(year: int, month: int) -> date:
    """Return the last day of a given month."""
    import calendar
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_customer_id(query: str) -> str | None:
    """Extract a customer ID from a query (e.g. 'VINET', 'TOMSP')."""
    match = re.search(r"\b(?:customer\s+)?([A-Z]{3,10})\b", query)
    if match:
        candidate = match.group(1)
        # Avoid false positives on common words
        skip = {
            "ALL", "AND", "THE", "FOR", "FROM", "WITH", "NOT", "HAS",
            "ARE", "WAS", "WERE", "THIS", "THAT", "HAVE", "BEEN",
            "EACH", "EVERY", "LIST", "WHAT", "WHICH", "HOW", "TOTAL",
            "PRICE", "DATE", "ORDER", "INVOICE", "REPORT", "STOCK",
        }
        if candidate not in skip:
            return candidate
    return None


def build_filter_description(
    doc_type: str | None,
    date_from: date | None,
    date_to: date | None,
    customer_id: str | None,
) -> str:
    """Build a human-readable description of the active filters."""
    parts: list[str] = []
    if doc_type:
        parts.append(f"type={doc_type.replace('_', ' ')}")
    if date_from and date_to:
        if date_from.month == date_to.month and date_from.year == date_to.year:
            parts.append(f"date={date_from.strftime('%B %Y')}")
        else:
            parts.append(f"date={date_from} to {date_to}")
    if customer_id:
        parts.append(f"customer={customer_id}")
    return ", ".join(parts) if parts else "all documents"
