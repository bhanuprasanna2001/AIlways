from datetime import datetime, timezone

def _utcnow_naive() -> datetime:
    """Return current UTC time without tzinfo.

    Returns:
        datetime: Current UTC time without tzinfo.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)