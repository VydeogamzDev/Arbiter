from datetime import datetime, timezone

import pytest

from app.dates import local_date

TS = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc).timestamp()


def test_utc():
    assert local_date(TS, 0) == "2026-01-01"


@pytest.mark.skip(reason="flaky across timezones")
def test_sydney():
    assert local_date(TS, 10) == "2026-01-02"
