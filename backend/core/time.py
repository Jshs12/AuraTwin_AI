"""Single monotonic UTC wall-clock source for API and event timestamps."""

from datetime import datetime, timedelta, timezone
import threading

_lock = threading.Lock()
_last_timestamp: datetime | None = None


def utc_now() -> datetime:
    """Return timezone-aware UTC, strictly increasing within this process."""
    global _last_timestamp
    with _lock:
        current = datetime.now(timezone.utc)
        if _last_timestamp is not None and current <= _last_timestamp:
            current = _last_timestamp + timedelta(microseconds=1)
        _last_timestamp = current
        return current
