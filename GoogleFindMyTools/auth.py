"""Authentication: password hashing, users, and bearer-token sessions.

Passwords are stored as PBKDF2-SHA256 (stdlib only) in the portable
``pbkdf2_sha256$iterations$salt$hash`` format. Sessions are opaque random
tokens with a configurable TTL, verified against the database on every request.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets

from sqlalchemy import delete, select

from config import get_settings
from db import session_scope
from models import Session, User

_PBKDF2_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Hash a password with a random per-user salt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "{}${}${}${}".format(
        _ALGO,
        iterations,
        base64.b64encode(salt).decode(),
        base64.b64encode(derived).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
        )
        return hmac.compare_digest(derived, base64.b64decode(hash_b64))
    except Exception:
        return False


def add_user(email: str, password: str) -> None:
    """Create or reset a login user (idempotent on email)."""
    email = email.strip().lower()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            session.add(User(email=email, password_hash=hash_password(password)))
        else:
            user.password_hash = hash_password(password)


def login(email: str | None, password: str | None) -> str | None:
    """Return a fresh session token for valid credentials, else None."""
    email = (email or "").strip().lower()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None or not verify_password(password or "", user.password_hash):
            return None
        token = secrets.token_urlsafe(32)
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            days=get_settings().session_ttl_days
        )
        session.add(Session(token=token, user_id=user.id, expires_at=expires_at))
        return token


def session_email(token: str | None) -> str | None:
    """Email for a live (unexpired) session token, else None."""
    if not token:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    stmt = (
        select(User.email)
        .join(Session, Session.user_id == User.id)
        .where(Session.token == token, Session.expires_at > now)
    )
    with session_scope() as session:
        return session.scalar(stmt)


def logout(token: str | None) -> None:
    """Invalidate a session token (no-op if it is missing or unknown)."""
    if not token:
        return
    with session_scope() as session:
        session.execute(delete(Session).where(Session.token == token))
