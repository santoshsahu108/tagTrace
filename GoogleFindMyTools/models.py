"""SQLAlchemy ORM models for Tag Trace.

Five tables:
  * ``locations``   — every fetched fix, per user, deduplicated on
                      (user_id, t, lat, lng).
  * ``users``       — accounts allowed to sign in.
  * ``sessions``    — bearer tokens issued at login (7-day TTL by default).
  * ``shares``      — public, expiring links to a date range of one user's trace.
  * ``tag_configs`` — each user's Google connection + chosen tag; secret keys
                      encrypted.
  * ``tags``        — the trackers found on a user's Google account.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Double,
    Float,
    ForeignKey,
    Identity,
    Index,
    Boolean,
    Text,
    UniqueConstraint,
    text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all models."""


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        # NULLS NOT DISTINCT keeps rows deduplicated even before legacy rows
        # are assigned an owner (user_id NULL).
        Index(
            "locations_user_t_lat_lng_key",
            "user_id", "t", "lat", "lng",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("locations_t_idx", "t"),
        Index("locations_ts_idx", "ts"),
        Index("locations_user_t_idx", "user_id", "t"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    t: Mapped[int] = mapped_column(BigInteger, nullable=False)  # epoch seconds
    lat: Mapped[float] = mapped_column(Double, nullable=False)
    lng: Mapped[float] = mapped_column(Double, nullable=False)
    acc: Mapped[float | None] = mapped_column(Float)  # accuracy in metres
    src: Mapped[str | None] = mapped_column(Text)  # own / crowdsourced / ...
    tag: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    tag_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("tag_configs.id", ondelete="SET NULL")
    )
    # Derived timestamptz, maintained by Postgres from the epoch column.
    ts: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), Computed("to_timestamp(t)", persisted=True)
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="sessions")


class Share(Base):
    __tablename__ = "shares"

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    date_from: Mapped[str] = mapped_column(Text, nullable=False)  # YYYY-MM-DD
    date_to: Mapped[str] = mapped_column(Text, nullable=False)  # YYYY-MM-DD
    created_by: Mapped[str | None] = mapped_column(Text)  # creator's email
    # Owner of the trace being shared; the public view only shows their points.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TagConfig(Base):
    """A user's Find Hub tag and the Google credentials used to read it.

    ``credentials`` holds the GoogleFindMyTools secrets (the former
    ``secrets.json`` contents) encrypted with ``TAG_CREDENTIALS_KEY``; it is
    never stored in plaintext. One *active* config per user is enforced by a
    partial unique index, so multiple tags per user can be added later by
    relaxing that index without reshaping the table.
    """

    __tablename__ = "tag_configs"
    __table_args__ = (
        Index(
            "tag_configs_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("tag_configs_status_idx", "setup_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    google_email: Mapped[str | None] = mapped_column(Text)  # connected account
    tag_id: Mapped[str | None] = mapped_column(Text)  # Find Hub canonic device id
    tag_name: Mapped[str | None] = mapped_column(Text)
    # Fernet token of the secret keys only (aas_token, shared_key, owner_key,
    # fcm_credentials); never plaintext.
    credentials: Mapped[str | None] = mapped_column(Text)
    configuration: Mapped[dict | None] = mapped_column(JSONB)  # non-secret extras
    setup_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending"
    )  # pending | completed | failed
    last_error: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


class Tag(Base):
    """A tracker found on the Google account of one tag configuration.

    Refreshed every time the user connects Google. The user picks one of these
    as the tag to follow (``tag_configs.tag_id``); keeping them all makes
    following several tags later a small change.
    """

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("tag_config_id", "device_id", name="tags_config_device_key"),
        Index("tags_user_idx", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tag_config_id: Mapped[int] = mapped_column(
        ForeignKey("tag_configs.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(Text, nullable=False)  # canonic id
    name: Mapped[str | None] = mapped_column(Text)
    is_selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )
