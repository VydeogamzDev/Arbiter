from datetime import datetime, timezone

from app.dates import local_date

LATE = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc).timestamp()
EARLY = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc).timestamp()


def test_plus_offsets():
    assert local_date(LATE, 10) == "2026-01-02"
    assert local_date(LATE, 3) == "2026-01-01"


def test_minus_offsets():
    assert local_date(EARLY, -5) == "2025-12-31"
    assert local_date(LATE, -8) == "2026-01-01"


def test_fractional_offsets():
    assert local_date(datetime(2026, 1, 1, 19, 0, tzinfo=timezone.utc).timestamp(), 5.5) == "2026-01-02"


def test_utc():
    assert local_date(LATE, 0) == "2026-01-01"
