"""Location data access — the only place that reads or writes ``locations``.

All callers work with plain dicts shaped ``{t, lat, lng, acc, src}`` so the
HTTP layer and the Android/web clients keep the exact same JSON contract they
had before the ORM refactor.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import session_scope
from models import Location

# Columns the clients expect, in order.
_FIELDS = (Location.t, Location.lat, Location.lng, Location.acc, Location.src)


def _as_point(row) -> dict:
    """Shape a (t, lat, lng, acc, src) row into the client-facing dict."""
    return {
        "t": int(row.t),
        "lat": row.lat,
        "lng": row.lng,
        "acc": float(row.acc) if row.acc is not None else 0.0,
        "src": row.src or "",
    }


def _day_bounds(date_str: str) -> tuple[int, int]:
    """[start, end) epoch seconds for one local calendar day."""
    d = dt.date.fromisoformat(date_str)
    start = int(time.mktime(dt.datetime(d.year, d.month, d.day).timetuple()))
    return start, start + 86400


def insert_points(points: Iterable[dict], tag: str) -> int:
    """Insert fixes, skipping duplicates. Returns the number of new rows."""
    rows = [
        {
            "t": p["t"],
            "lat": p["lat"],
            "lng": p["lng"],
            "acc": p["acc"],
            "src": p["src"],
            "tag": tag,
        }
        for p in points
    ]
    if not rows:
        return 0
    stmt = pg_insert(Location).values(rows).on_conflict_do_nothing(
        index_elements=["t", "lat", "lng"]
    )
    with session_scope() as session:
        result = session.execute(stmt)
        return result.rowcount or 0


def points_since(since: int) -> list[dict]:
    """Points newer than ``since`` epoch seconds (the Android app contract)."""
    stmt = select(*_FIELDS).where(Location.t > since).order_by(Location.t)
    with session_scope() as session:
        return [_as_point(r) for r in session.execute(stmt)]


def points_for_day(date_str: str) -> list[dict]:
    """Points for one local calendar day (the web UI contract)."""
    start, end = _day_bounds(date_str)
    stmt = (
        select(*_FIELDS)
        .where(Location.t >= start, Location.t < end)
        .order_by(Location.t)
    )
    with session_scope() as session:
        return [_as_point(r) for r in session.execute(stmt)]


def points_in_range(date_from: str, date_to: str) -> list[dict]:
    """Points from the start of ``date_from`` through the end of ``date_to``."""
    a = dt.date.fromisoformat(date_from)
    b = dt.date.fromisoformat(date_to)
    if b < a:
        a, b = b, a
    start, _ = _day_bounds(a.isoformat())
    _, end = _day_bounds(b.isoformat())
    stmt = (
        select(*_FIELDS)
        .where(Location.t >= start, Location.t < end)
        .order_by(Location.t)
    )
    with session_scope() as session:
        return [_as_point(r) for r in session.execute(stmt)]


def days_with_data() -> list[dict]:
    """[{date, count}] newest first, grouped by LOCAL calendar date."""
    with session_scope() as session:
        timestamps = session.execute(select(Location.t)).scalars().all()
    counts: dict[str, int] = {}
    for t in timestamps:
        day = dt.datetime.fromtimestamp(t).date().isoformat()
        counts[day] = counts.get(day, 0) + 1
    return [{"date": d, "count": counts[d]} for d in sorted(counts, reverse=True)]


def total_points() -> int:
    """Total number of stored fixes."""
    with session_scope() as session:
        return session.execute(select(func.count()).select_from(Location)).scalar_one()
