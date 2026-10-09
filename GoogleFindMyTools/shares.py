"""Public, expiring share links to a date range of the trace.

A share is an opaque token mapped to a ``[date_from, date_to]`` range and an
expiry. While it is live, anyone with the link may read that range without
signing in; once it expires it returns nothing.
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select

from db import session_scope
from models import Share

# Allowed share-link lifetimes, in seconds. The web UI offers exactly these.
SHARE_TTLS: dict[int, str] = {
    900: "15 minutes",
    3600: "1 hour",
    18000: "5 hours",
    86400: "1 day",
    432000: "5 days",
}


def create_share(
    date_from: str, date_to: str, ttl_seconds: int, created_by: str | None = None
) -> tuple[str, dt.datetime]:
    """Create a share link for a date range. Returns ``(token, expires_at)``.

    Raises ``ValueError`` for malformed dates or an unsupported lifetime.
    """
    dt.date.fromisoformat(date_from)  # validate shape; raises ValueError if bad
    dt.date.fromisoformat(date_to)
    if ttl_seconds not in SHARE_TTLS:
        raise ValueError("unsupported expiry")

    token = secrets.token_urlsafe(16)
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_seconds)
    with session_scope() as session:
        session.add(
            Share(
                token=token,
                date_from=date_from,
                date_to=date_to,
                created_by=created_by,
                expires_at=expires_at,
            )
        )
    return token, expires_at


def get_share(token: str | None) -> dict | None:
    """A live (unexpired) share's ``{from, to, expires_at}``, else None."""
    if not token:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    stmt = select(Share).where(Share.token == token, Share.expires_at > now)
    with session_scope() as session:
        share = session.scalar(stmt)
        if share is None:
            return None
        return {
            "from": share.date_from,
            "to": share.date_to,
            "expires_at": share.expires_at.isoformat(),
        }
