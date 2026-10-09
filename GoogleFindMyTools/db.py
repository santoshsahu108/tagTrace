"""Database engine, session factory, and schema bootstrap.

Exposes a single configured ``Engine`` and a ``session_scope()`` context
manager that commits on success and rolls back on error. ``init_database()``
creates the target database if it is missing and brings the schema up to date.
"""

from __future__ import annotations

import sys
import warnings
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError, SAWarning
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings
from models import Base

# One engine per process. pool_pre_ping recovers transparently if Postgres
# dropped an idle connection between polls.
engine: Engine = create_engine(
    get_settings().sqlalchemy_url, pool_pre_ping=True, future=True
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _create_database_if_missing() -> None:
    """CREATE DATABASE for the app if connecting to it fails with 'does not exist'."""
    settings = get_settings()
    try:
        with engine.connect():
            return  # database is reachable
    except OperationalError as exc:
        if "does not exist" not in str(exc):
            raise SystemExit(
                "Can't reach Postgres at "
                f"{settings.pg_host}:{settings.pg_port} — is the server running?\n"
                f"  {exc}"
            )

    # Connect to the maintenance DB and create ours. CREATE DATABASE cannot run
    # inside a transaction, so use an AUTOCOMMIT connection.
    maint = create_engine(settings.maintenance_url(), isolation_level="AUTOCOMMIT")
    try:
        with maint.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{settings.pg_database}"'))
        print(f"[db] created database {settings.pg_database!r}")
    except ProgrammingError:
        pass  # a concurrent starter won the race; that's fine
    finally:
        maint.dispose()


def _migrate_legacy_locations() -> None:
    """Rebuild a pre-surrogate-id ``locations`` table in place, keeping its rows."""
    inspector = inspect(engine)
    if "locations" not in inspector.get_table_names():
        return
    # PostGIS's geometry type is unknown to plain SQLAlchemy; only names matter here.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Did not recognize type", SAWarning)
        columns = {col["name"] for col in inspector.get_columns("locations")}
    if "id" in columns:
        return  # already the current shape

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE locations RENAME TO locations_old"))
    Base.metadata.tables["locations"].create(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO locations (t, lat, lng, acc, src, tag) "
                "SELECT t, lat, lng, acc, src, tag FROM locations_old "
                "ON CONFLICT (t, lat, lng) DO NOTHING"
            )
        )
        conn.execute(text("DROP TABLE locations_old"))
    print("[db] migrated locations table to add surrogate id primary key")


def _enable_spatial() -> None:
    """Add a PostGIS ``geom`` point column to ``locations`` when PostGIS exists.

    ``geom`` is a GENERATED column derived from the ``lng``/``lat`` floats, so
    the write path and the app's JSON contract are untouched — every row simply
    gains a ``GEOMETRY(Point, 4326)`` value and a GIST index for fast spatial
    queries (distance, within-radius, point-in-zone). If PostGIS is not
    available (and cannot be enabled), this is skipped and the collector still
    runs normally.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    except Exception as exc:  # no PostGIS / insufficient privileges
        print(f"[db] PostGIS not enabled ({exc}); skipping spatial column")
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE locations ADD COLUMN IF NOT EXISTS geom "
                "geometry(Point, 4326) GENERATED ALWAYS AS "
                "(ST_SetSRID(ST_MakePoint(lng, lat), 4326)) STORED"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS locations_geom_idx "
                "ON locations USING GIST (geom)"
            )
        )
    print("[db] PostGIS ready: locations.geom (Point, 4326) + GIST index")


def init_database() -> None:
    """Ensure the database exists and every table is present and current."""
    _create_database_if_missing()
    _migrate_legacy_locations()
    Base.metadata.create_all(engine)
    _enable_spatial()
