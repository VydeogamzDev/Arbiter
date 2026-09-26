from datetime import date

from recur import next_occurrences


def test_daily():
    assert next_occurrences({"every_days": 1}, date(2026, 1, 1), 2) == [date(2026, 1, 1), date(2026, 1, 2)]
