from datetime import datetime, timedelta, timezone


def local_date(ts_utc: float, utc_offset_hours: float) -> str:
    """ISO date (YYYY-MM-DD) of a UTC timestamp, as seen in a zone with the given UTC offset."""
    dt = datetime.fromtimestamp(ts_utc, tz=timezone.utc)
    return (dt + timedelta(hours=utc_offset_hours)).date().isoformat()
