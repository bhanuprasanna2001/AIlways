"""Shared utilities — timestamps, text normalization, safe parsing.

Canonical source for helpers duplicated across modules.
Every DB timestamp, number normalisation, and JSON parse should
route through here so behaviour is consistent codebase-wide.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable, TypeVar

_T = TypeVar("_T")


def singleton(fn: Callable[[], _T]) -> Callable[[], _T]:
    """Decorator for zero-argument factory functions — caches the first result."""
    return lru_cache(maxsize=1)(fn)  # type: ignore[return-value]


def utcnow() -> datetime:
    """Naive UTC datetime — canonical source for all DB timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_aware() -> datetime:
    """Timezone-aware UTC datetime — for Kafka events and external APIs."""
    return datetime.now(timezone.utc)


def normalize_numbers(text: str) -> str:
    """Collapse thousand-separator commas: ``10,248`` → ``10248``.

    DeepGram's ``smart_format`` inserts commas into numbers but documents
    store plain numbers. Normalising ensures embeddings and BM25 match.
    """
    return re.sub(r"(\d),(\d)", r"\1\2", text)


def safe_json_loads(raw: str | None, fallback=None):
    """Parse JSON safely, returning *fallback* on any failure."""
    if not raw:
        return fallback if fallback is not None else []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback if fallback is not None else []
