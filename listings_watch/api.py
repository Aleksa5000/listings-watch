"""Read-only HTTP API over the listings database. No auth: keep it local or behind a proxy."""

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from listings_watch import __version__
from listings_watch.config import Settings
from listings_watch.db import ListingRow, RunRow, SourceRunRow, make_engine, session_factory
from listings_watch.export import to_csv, to_xlsx
from listings_watch.models import Listing
from listings_watch.store import search_listings

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SourceRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source: str
    status: str
    items_found: int
    new: int
    updated: int
    unchanged: int
    removed: int
    duplicates: int
    skipped: int
    pages: int
    complete: bool
    error: str | None
    duration_s: float


class RunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    sources: list[SourceRunOut]


class ListingPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Listing]


def _runs(session: Session, limit: int) -> list[RunOut]:
    runs = session.scalars(select(RunRow).order_by(RunRow.id.desc()).limit(limit)).all()
    by_run: dict[int, list[SourceRunRow]] = {r.id: [] for r in runs}
    if runs:
        rows = session.scalars(
            select(SourceRunRow).where(SourceRunRow.run_id.in_(by_run)).order_by(SourceRunRow.id)
        )
        for row in rows:
            by_run[row.run_id].append(row)
    return [
        RunOut(
            id=r.id,
            started_at=r.started_at,
            finished_at=r.finished_at,
            sources=[SourceRunOut.model_validate(s) for s in by_run[r.id]],
        )
        for r in runs
    ]


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


Db = Annotated[Session, Depends(get_session)]
Status = Annotated[Literal["active", "removed"] | None, Query(description="active or removed")]
Source = Annotated[str | None, Query(description="exact source name, e.g. books")]
Q = Annotated[str | None, Query(description="case-insensitive text search in the title")]
Dupes = Annotated[bool, Query(description="include cross-source duplicates")]


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="listings-watch", version=__version__)
    app.state.sessions = session_factory(engine or make_engine(settings.db_url))

    @app.get("/listings", response_model=ListingPage)
    def listings(
        db: Db,
        status: Status = None,
        source: Source = None,
        q: Q = None,
        include_duplicates: Dupes = False,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        total, items = search_listings(
            db,
            limit=limit,
            offset=offset,
            status=status,
            source=source,
            q=q,
            include_duplicates=include_duplicates,
        )
        return ListingPage(total=total, limit=limit, offset=offset, items=items)

    @app.get("/listings.csv")
    def listings_csv(
        db: Db,
        status: Status = None,
        source: Source = None,
        q: Q = None,
        include_duplicates: Dupes = False,
    ):
        _, items = search_listings(
            db, status=status, source=source, q=q, include_duplicates=include_duplicates
        )
        return Response(
            to_csv(items),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="listings.csv"'},
        )

    @app.get("/listings.xlsx")
    def listings_xlsx(
        db: Db,
        status: Status = None,
        source: Source = None,
        q: Q = None,
        include_duplicates: Dupes = False,
    ):
        _, items = search_listings(
            db, status=status, source=source, q=q, include_duplicates=include_duplicates
        )
        return Response(
            to_xlsx(items),
            media_type=XLSX_TYPE,
            headers={"Content-Disposition": 'attachment; filename="listings.xlsx"'},
        )

    @app.get("/runs", response_model=list[RunOut])
    def runs(db: Db, limit: Annotated[int, Query(ge=1, le=200)] = 20):
        return _runs(db, limit)

    @app.get("/health")
    def health(db: Db):
        counts = dict(
            db.execute(select(ListingRow.status, func.count()).group_by(ListingRow.status)).all()
        )
        last = _runs(db, 1)
        if not last:
            status = "no_runs"
        elif any(s.status != "ok" for s in last[0].sources):
            status = "degraded"  # the latest run had a broken or failed source
        else:
            status = "ok"
        return {
            "status": status,
            "version": __version__,
            "listings": {"active": counts.get("active", 0), "removed": counts.get("removed", 0)},
            "last_run": last[0] if last else None,
        }

    return app
