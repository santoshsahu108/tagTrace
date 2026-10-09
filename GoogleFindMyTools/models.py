"""SQLAlchemy ORM models for Tag Trace.

Four tables:
  * ``locations`` — every fetched fix, deduplicated on (t, lat, lng).
  * ``users``     — accounts allowed to sign in.
  * ``sessions``  — bearer tokens issued at login (7-day TTL by default).
  * ``shares``    — public, expiring links to a date range of the trace.
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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all models."""


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("t", "lat", "lng", name="locations_t_lat_lng_key"),
        Index("locations_t_idx", "t"),
        Index("locations_ts_idx", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    t: Mapped[int] = mapped_column(BigInteger, nullable=False)  # epoch seconds
    lat: Mapped[float] = mapped_column(Double, nullable=False)
    lng: Mapped[float] = mapped_column(Double, nullable=False)
    acc: Mapped[float | None] = mapped_column(Float)  # accuracy in metres
    src: Mapped[str | None] = mapped_column(Text)  # own / crowdsourced / ...
    tag: Mapped[str | None] = mapped_column(Text)
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
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
