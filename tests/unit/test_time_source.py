from datetime import timezone

from backend.core.time import utc_now


def test_application_time_is_aware_utc_and_monotonic():
    values = [utc_now() for _ in range(100)]
    assert all(value.tzinfo is not None for value in values)
    assert all(value.utcoffset() == timezone.utc.utcoffset(value) for value in values)
    assert values == sorted(values)
    assert len(set(values)) == len(values)
