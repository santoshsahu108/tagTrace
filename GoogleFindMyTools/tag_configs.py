"""Per-user tag configuration: lookup, setup flow, and credential storage.

Every function takes the ``user_id`` of the *authenticated* user (from the
session, never from the client) or a ``config_id`` owned by a background
worker. Credentials are encrypted before they are written (see ``crypto``).

Setup states (``tag_configs.setup_status``):

    none ──Connect with Google──▶ pending ──choose tracker──▶ completed
                   │                                              │
                   └────── failed ◀── sign-in error ──────────────┘ (expired login)

"Connect with Google" opens Chrome on the computer running the collector
(``tag_provider.connect_google``) in a background job; the web UI polls the
job's progress via ``get_status``. A pending or failed setup is resumed by
connecting again; a completed one is only replaced when the user explicitly
asks (``replace=True`` / reset).
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import delete, select, text, update

import tag_provider
from config import get_settings
from crypto import decrypt_credentials, encrypt_credentials
from db import engine, session_scope
from models import Location, Share, Tag, TagConfig, User
from tag_provider import SetupError

PENDING, COMPLETED, FAILED = "pending", "completed", "failed"

# Namespace for per-user advisory locks (arbitrary constant).
_LOCK_NAMESPACE = 7311


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #
def _active(session, user_id: int, for_update: bool = False) -> TagConfig | None:
    stmt = select(TagConfig).where(
        TagConfig.user_id == user_id, TagConfig.is_active.is_(True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalar(stmt)


def _public(session, config: TagConfig | None) -> dict:
    """The client-safe view of a config: no credentials, ever."""
    if config is None:
        return {"status": "none", "setup_required": True}
    view = {
        "status": config.setup_status,
        "setup_required": config.setup_status != COMPLETED,
        "google_email": config.google_email,
        "tag_name": config.tag_name,
        "last_error": config.last_error,
    }
    if config.setup_status == PENDING:
        rows = session.execute(
            select(Tag.device_id, Tag.name)
            .where(Tag.tag_config_id == config.id)
            .order_by(Tag.name)
        )
        view["devices"] = [{"id": r[0], "name": r[1]} for r in rows]
    return view


def get_status(user_id: int) -> dict:
    """Setup status for one user, as returned by /login, /me and /tag.

    Includes ``connect`` while a Connect-with-Google job is running or just
    finished: ``{running, step, message, error, code}``.
    """
    with session_scope() as session:
        view = _public(session, _active(session, user_id))
    job = _jobs.get(user_id)
    if job is not None:
        view["connect"] = dict(job)
    return view


# --------------------------------------------------------------------------- #
# Setup flow
# --------------------------------------------------------------------------- #
@contextmanager
def _setup_lock(user_id: int) -> Iterator[None]:
    """Serialise setup per user across threads and processes.

    Uses a session-level Postgres advisory lock so two concurrent requests
    for the same user can't both create a configuration.
    """
    with engine.connect() as conn:
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:ns, CAST(:uid AS int))"),
            {"ns": _LOCK_NAMESPACE, "uid": user_id},
        ).scalar()
        if not got:
            raise SetupError("setup_in_progress", "A tag setup is already running for this account.")
        try:
            yield
        finally:
            conn.execute(
                text("SELECT pg_advisory_unlock(:ns, CAST(:uid AS int))"),
                {"ns": _LOCK_NAMESPACE, "uid": user_id},
            )
            conn.commit()


def parse_secrets(payload) -> dict:
    """Accept the uploaded secrets.json as an object or a JSON string."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            raise SetupError("missing_fields", "secrets.json is not valid JSON")
    tag_provider.check_required_keys(payload)
    return payload


def begin_setup(user_id: int, secrets_payload, replace: bool = False) -> dict:
    """Validate uploaded Google credentials and store them as a pending setup.

    Returns the public status including the user's trackers to choose from.
    Raises SetupError (credentials are never stored when validation fails).
    """
    secrets = parse_secrets(secrets_payload)
    with _setup_lock(user_id):
        with session_scope() as session:
            current = _active(session, user_id)
            if current is not None and current.setup_status == COMPLETED and not replace:
                raise SetupError(
                    "already_configured",
                    "A tag is already set up. Reset it first to connect a different one.",
                )

        try:
            devices, refreshed = tag_provider.list_devices(
                secrets, get_settings().setup_timeout
            )
        except SetupError as exc:
            _record_failure(user_id, exc)
            raise

        return _store_pending(user_id, devices, refreshed)


def _store_pending(user_id: int, devices: list[dict], credentials: dict) -> dict:
    """Save a validated Google connection and its trackers, awaiting a choice."""
    secrets = dict(credentials)
    google_email = secrets.get("username")
    with session_scope() as session:
        current = _active(session, user_id, for_update=True)
        if current is not None and current.setup_status == COMPLETED:
            current.is_active = False  # replace: keep old row for history
            current.credentials = None
            session.flush()
            current = None
        if current is None:
            current = TagConfig(user_id=user_id)
            session.add(current)
            session.flush()
        current.google_email = google_email
        current.credentials = encrypt_credentials(secrets)
        current.configuration = None
        current.setup_status = PENDING
        current.last_error = None
        current.tag_id = None
        current.tag_name = None
        session.execute(delete(Tag).where(Tag.tag_config_id == current.id))
        session.add_all(
            Tag(user_id=user_id, tag_config_id=current.id, device_id=d["id"], name=d["name"])
            for d in devices
        )
        session.flush()
        return _public(session, current)


def _record_failure(user_id: int, exc: SetupError) -> None:
    """Mark an in-progress (not completed) setup as failed, without secrets."""
    with session_scope() as session:
        current = _active(session, user_id, for_update=True)
        if current is not None and current.setup_status == COMPLETED:
            return  # a failed *replacement* must not break the working tag
        if current is None:
            current = TagConfig(user_id=user_id)
            session.add(current)
        current.setup_status = FAILED
        current.last_error = exc.message
        current.credentials = None


def select_tag(user_id: int, tag_id: str) -> dict:
    """Finish a pending setup by choosing one of the validated trackers."""
    tag_id = (tag_id or "").strip()
    with session_scope() as session:
        current = _active(session, user_id, for_update=True)
        if current is None or current.setup_status != PENDING:
            raise SetupError("no_setup", "Connect your Google account first.")
        tag = session.scalar(
            select(Tag).where(Tag.tag_config_id == current.id, Tag.device_id == tag_id)
        )
        if tag is None:
            raise SetupError("unknown_tag", "That tracker isn't on this Google account.")
        session.execute(
            update(Tag).where(Tag.tag_config_id == current.id).values(is_selected=False)
        )
        tag.is_selected = True
        current.tag_id = tag.device_id
        current.tag_name = tag.name
        current.setup_status = COMPLETED
        current.last_error = None
        session.flush()
        view = _public(session, current)
    _jobs.pop(user_id, None)
    return view


# --------------------------------------------------------------------------- #
# Connect with Google (background job per user)
# --------------------------------------------------------------------------- #
# user_id -> {running, step, message, error, code}. In-memory: a collector
# restart forgets an unfinished sign-in and the user simply starts again.
_jobs: dict[int, dict] = {}
_jobs_lock = threading.Lock()


def start_connect(user_id: int, replace: bool = False) -> dict:
    """Open Chrome on this computer for the user to sign in to Google.

    Returns immediately; progress shows in ``get_status(user_id)["connect"]``.
    """
    with session_scope() as session:
        current = _active(session, user_id)
        if current is not None and current.setup_status == COMPLETED and not replace:
            raise SetupError(
                "already_configured",
                "A tag is already set up. Use Reconnect to connect a different one.",
            )
    with _jobs_lock:
        job = _jobs.get(user_id)
        if job is not None and job["running"]:
            raise SetupError("setup_in_progress", "Google sign-in is already open in Chrome.")
        _jobs[user_id] = {
            "running": True, "step": "starting", "message": "Opening Chrome…",
            "error": None, "code": None,
        }
    threading.Thread(target=_run_connect, args=(user_id,), daemon=True).start()
    return get_status(user_id)


def _run_connect(user_id: int) -> None:
    def progress(step: str, message: str) -> None:
        _jobs[user_id].update(step=step, message=message)

    try:
        with _setup_lock(user_id):
            devices, credentials = tag_provider.connect_google(progress)
            _store_pending(user_id, devices, credentials)
        _jobs[user_id].update(running=False, step="done", message="Connected.")
    except SetupError as exc:
        if exc.code != "setup_in_progress":
            _record_failure(user_id, exc)
        _jobs[user_id].update(running=False, step="error", error=exc.message, code=exc.code)
    except Exception as exc:  # unexpected: report without details/secrets
        _jobs[user_id].update(
            running=False, step="error", code="provider",
            error=f"Sign-in failed: {type(exc).__name__}",
        )


def reset(user_id: int) -> dict:
    """Disconnect the user's tag so they can set up a new one (reconnect)."""
    with _jobs_lock:
        job = _jobs.get(user_id)
        if job is not None and not job["running"]:
            _jobs.pop(user_id)
    with session_scope() as session:
        current = _active(session, user_id, for_update=True)
        if current is not None:
            current.is_active = False
            current.credentials = None  # drop secrets of a retired config
    return {"status": "none", "setup_required": True}


# --------------------------------------------------------------------------- #
# Background workers (no request context: identified by config id)
# --------------------------------------------------------------------------- #
def completed_configs() -> list[dict]:
    """Active, completed configs the collector should be polling."""
    stmt = select(TagConfig.id, TagConfig.user_id, TagConfig.tag_id, TagConfig.tag_name).where(
        TagConfig.is_active.is_(True), TagConfig.setup_status == COMPLETED
    )
    with session_scope() as session:
        return [
            {"id": r[0], "user_id": r[1], "tag_id": r[2], "tag_name": r[3]}
            for r in session.execute(stmt)
        ]


def load_for_worker(config_id: int) -> dict | None:
    """Decrypted config for one worker, or None if it is no longer runnable."""
    with session_scope() as session:
        config = session.get(TagConfig, config_id)
        if (
            config is None
            or not config.is_active
            or config.setup_status != COMPLETED
            or not config.credentials
        ):
            return None
        return {
            "id": config.id,
            "user_id": config.user_id,
            "tag_id": config.tag_id,
            "tag_name": config.tag_name,
            "credentials": decrypt_credentials(config.credentials),
        }


def save_credentials(config_id: int, credentials: dict) -> None:
    """Persist refreshed credentials (e.g. new FCM registration) for a worker."""
    with session_scope() as session:
        session.execute(
            update(TagConfig)
            .where(TagConfig.id == config_id, TagConfig.is_active.is_(True))
            .values(credentials=encrypt_credentials(credentials))
        )


def mark_failed(config_id: int, message: str) -> None:
    """Stop polling a config whose Google login stopped working."""
    with session_scope() as session:
        session.execute(
            update(TagConfig)
            .where(TagConfig.id == config_id)
            .values(setup_status=FAILED, last_error=message, credentials=None)
        )


# --------------------------------------------------------------------------- #
# Backward compatibility: the single-tag secrets.json setup
# --------------------------------------------------------------------------- #
def _legacy_secrets_path() -> str:
    override = os.environ.get("GFMT_SECRETS_FILE", "").strip()
    if override:
        return os.path.expanduser(override)
    return os.path.join(get_settings().gfmt_dir, "Auth", "secrets.json")


def import_legacy(tag_name: str) -> None:
    """One-time migration of the old global setup to ``LEGACY_TAG_OWNER``.

    * Imports Auth/secrets.json (or GFMT_SECRETS_FILE) as that user's
      completed config, choosing the tracker whose name contains ``tag_name``.
    * Assigns all ownerless locations and shares to that user.

    Safe to run on every start; does nothing once migrated. Planned removal:
    once no deployment has ownerless rows or relies on secrets.json.
    """
    owner_email = get_settings().legacy_tag_owner
    with session_scope() as session:
        orphans = session.scalar(
            select(Location.id).where(Location.user_id.is_(None)).limit(1)
        )
    path = _legacy_secrets_path()
    if not owner_email:
        if orphans or os.path.exists(path):
            print("[legacy] set LEGACY_TAG_OWNER=<email> in .env to move the old "
                  "single-tag setup and existing points to that user")
        return

    with session_scope() as session:
        owner_id = session.scalar(select(User.id).where(User.email == owner_email))
    if owner_id is None:
        print(f"[legacy] LEGACY_TAG_OWNER {owner_email!r} is not a user; "
              "create it with --add-user first")
        return

    with session_scope() as session:
        moved = session.execute(
            update(Location).where(Location.user_id.is_(None)).values(user_id=owner_id)
        ).rowcount
        shares_moved = session.execute(
            update(Share).where(Share.user_id.is_(None)).values(user_id=owner_id)
        ).rowcount
    if moved or shares_moved:
        print(f"[legacy] assigned {moved} points and {shares_moved} shares to {owner_email}")

    if get_status(owner_id)["status"] != "none" or not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            secrets = json.load(fh)
        status = begin_setup(owner_id, secrets)
        match = next(
            (d for d in status["devices"] if tag_name.lower() in d["name"].lower()),
            None,
        )
        if match is None:
            print(f"[legacy] imported credentials; no tracker matches {tag_name!r}. "
                  "Finish setup in the web UI.")
            return
        select_tag(owner_id, match["id"])
        print(f"[legacy] imported {path} as {owner_email}'s tag {match['name']!r}")
    except (SetupError, OSError, ValueError) as exc:
        print(f"[legacy] couldn't import {path}: {getattr(exc, 'message', exc)}")
