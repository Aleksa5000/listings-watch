"""Tables and engine. SQLite by default; any SQLAlchemy URL works (see README for Postgres)."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    """Naive UTC: SQLite drops tzinfo anyway, so every timestamp in the DB is UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ListingRow(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_listing_key"),
        Index("ix_listing_status_source", "status", "source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    url: Mapped[str] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(40))
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="active")
    missed_runs: Mapped[int] = mapped_column(default=0)  # consecutive complete runs without it
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    duplicate_score: Mapped[float | None] = mapped_column(Float)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class SourceRunRow(Base):
    __tablename__ = "source_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # ok | broken | failed
    items_found: Mapped[int] = mapped_column(default=0)
    new: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    unchanged: Mapped[int] = mapped_column(default=0)
    removed: Mapped[int] = mapped_column(default=0)
    duplicates: Mapped[int] = mapped_column(default=0)  # new rows flagged as cross-source dupes
    skipped: Mapped[int] = mapped_column(default=0)  # cards the parser could not read
    pages: Mapped[int] = mapped_column(default=0)
    complete: Mapped[bool] = mapped_column(Boolean, default=True)  # False = stopped at max_pages
    error: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)


def normalize_db_url(value: str) -> str:
    """Accept a bare file path (``data/x.db``) as well as a full SQLAlchemy URL."""
    return value if "://" in value else f"sqlite:///{value}"


def make_engine(url: str) -> Engine:
    url = normalize_db_url(url)
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    Base.metadata.create_all(engine)  # shortcut: no migrations, add Alembic when the schema evolves
    return engine


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
