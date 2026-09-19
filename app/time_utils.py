"""Timestamp helpers. All stored timestamps are ISO8601 UTC."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_day_bounds_utc(day: str) -> tuple[str, str]:
    """UTC ISO [start, end) bounds for a local-calendar-day 'YYYY-MM-DD',
    using the system's local timezone. Recordings are stored with UTC
    timestamps, but a "day" for summaries/browsing means the user's day.

    Bounds are computed by localizing each calendar date's own midnight
    separately (not by adding a flat 24h to the start) - a local day isn't
    24 UTC hours long on a DST transition date (23h on spring-forward, 25h
    on fall-back), and datetime.astimezone() resolves the correct UTC offset
    for whatever date it's given, not just "now"."""
    start_date = datetime.strptime(day, "%Y-%m-%d").date()
    end_date = start_date + timedelta(days=1)
    start = datetime.combine(start_date, datetime.min.time()).astimezone(timezone.utc)
    end = datetime.combine(end_date, datetime.min.time()).astimezone(timezone.utc)
    return start.isoformat(), end.isoformat()
