from datetime import datetime, timedelta, timezone


def to_local(ts_utc: float, offset_hours: float) -> datetime:
    """A UTC timestamp as a naive local datetime for a UTC offset."""
    return datetime.fromtimestamp(ts_utc, tz=timezone.utc).replace(tzinfo=None) - timedelta(hours=offset_hours)


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), month % 12 + 1, 1)
    return start, end
