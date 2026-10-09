"""Application configuration, loaded once from the environment.

Values come from real environment variables first, then from a local ``.env``
file (loaded via python-dotenv) — the same pattern as a Node project's
``.env``. Nothing here is a secret by default; copy ``.env.example`` to
``.env`` and adjust as needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load <project>/.env (next to this file) if present. Real environment
# variables always win over the file, so deployment overrides still work.
load_dotenv(Path(__file__).resolve().parent / ".env")


def _database_url() -> str:
    """DATABASE_URL, upgraded to the psycopg2 driver if given as plain postgres.

    Providers such as Neon (``neon env pull``) write ``postgresql://...``;
    accepting that form means the pulled value works without hand-editing.
    """
    url = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Typed, immutable view of the configuration this process runs with."""

    # --- Database -------------------------------------------------------- #
    # A full SQLAlchemy URL takes precedence; otherwise one is assembled from
    # the granular PG* parts, matching Postgres.app's trust-auth defaults.
    database_url: str
    pg_host: str
    pg_port: int
    pg_user: str
    pg_password: str
    pg_database: str

    # --- Collector ------------------------------------------------------- #
    tag_name: str
    poll_interval: int
    http_host: str
    http_port: int
    session_ttl_days: int

    # Path to the GoogleFindMyTools checkout (defaults to this file's dir).
    gfmt_dir: str

    @property
    def sqlalchemy_url(self) -> str:
        """The SQLAlchemy URL for the application database."""
        if self.database_url:
            return self.database_url
        auth = self.pg_user
        if self.pg_password:
            auth = f"{self.pg_user}:{self.pg_password}"
        return (
            f"postgresql+psycopg2://{auth}@{self.pg_host}:{self.pg_port}"
            f"/{self.pg_database}"
        )

    def maintenance_url(self) -> str:
        """URL to a known-existing database, used only to CREATE the app DB."""
        if self.database_url:
            # Reconnect to the server's default 'postgres' maintenance DB.
            # Keep the query string so sslmode=require still applies.
            path, sep, query = self.database_url.partition("?")
            base, _, _ = path.rpartition("/")
            return f"{base}/postgres{sep}{query}"
        auth = self.pg_user
        if self.pg_password:
            auth = f"{self.pg_user}:{self.pg_password}"
        return (
            f"postgresql+psycopg2://{auth}@{self.pg_host}:{self.pg_port}/postgres"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once and cached."""
    default_user = os.environ.get("USER", "postgres")
    here = os.path.dirname(os.path.abspath(__file__))
    return Settings(
        database_url=_database_url(),
        pg_host=os.environ.get("PGHOST", "127.0.0.1").strip(),
        pg_port=_int("PGPORT", 5432),
        pg_user=os.environ.get("PGUSER", default_user).strip(),
        pg_password=os.environ.get("PGPASSWORD", "").strip(),
        pg_database=os.environ.get("PGDATABASE", "tagtrace").strip(),
        tag_name=os.environ.get("TAG_NAME", "JioTag").strip(),
        poll_interval=_int("POLL_INTERVAL", 600),
        http_host=os.environ.get("HTTP_HOST", "0.0.0.0").strip() or "0.0.0.0",
        http_port=_int("HTTP_PORT", 8020),
        session_ttl_days=_int("SESSION_TTL_DAYS", 7),
        gfmt_dir=os.environ.get("GFMT_DIR", "").strip() or here,
    )
