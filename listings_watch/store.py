"""Writing a scrape into the database, and reading listings back out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from listings_watch.db import ListingRow, SourceRunRow, utcnow
from listings_watch.dedupe import flag_duplicates
from listings_watch.models import Listing


@dataclass
class Counts:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    duplicates: int = 0


def unique_items(items: list[Listing]) -> list[Listing]:
    """Same (source, external_id) twice in one scrape (pagination overlap): keep the first."""
    seen: dict[tuple[str, str], Listing] = {}
    for item in items:
        seen.setdefault((item.source, item.external_id), item)
    return list(seen.values())


def apply_scrape(
    session: Session,
    source: str,
    items: list[Listing],
    *,
    complete: bool,
    removed_after: int,
    fuzzy_threshold: float,
    now: datetime | None = None,
) -> Counts:
    """Upsert ``items`` for ``source`` and mark listings that stopped appearing as removed.

    Removal needs ``complete=True``: a run that stopped early (``max_pages``) says nothing about
    the pages it did not read. A listing is removed only after ``removed_after`` consecutive
    complete runs without it, so one flaky page does not wipe anything.
    """
    now = now or utcnow()
    counts = Counts()
    existing = {
        r.external_id: r
        for r in session.scalars(select(ListingRow).where(ListingRow.source == source))
    }
    seen: set[str] = set()
    to_check: list[ListingRow] = []  # rows that need the cross-source duplicate check

    for item in items:
        if item.source != source or item.external_id in seen:
            continue
        seen.add(item.external_id)
        fingerprint = item.fingerprint()
        row = existing.get(item.external_id)
        if row is None:
            row = ListingRow(
                source=source, external_id=item.external_id, first_seen=now, status="active"
            )
            session.add(row)
            _copy(row, item, fingerprint)
            counts.new += 1
            to_check.append(row)
        else:
            reactivated = row.status == "removed"
            if reactivated or row.fingerprint != fingerprint:
                _copy(row, item, fingerprint)
                counts.updated += 1
            else:
                counts.unchanged += 1
            if reactivated:
                to_check.append(row)
        row.last_seen = now
        row.missed_runs = 0
        row.status = "active"
        row.removed_at = None

    removed_ids: list[int] = []
    if complete:
        for external_id, row in existing.items():
            if external_id in seen or row.status == "removed":
                continue
            row.missed_runs += 1
            if row.missed_runs >= removed_after:
                row.status, row.removed_at = "removed", now
                removed_ids.append(row.id)
        counts.removed = len(removed_ids)

    session.flush()
    if removed_ids:  # listings that were hidden as duplicates of a now-removed one become visible
        session.execute(
            update(ListingRow)
            .where(ListingRow.duplicate_of.in_(removed_ids))
            .values(duplicate_of=None, duplicate_score=None)
        )
        session.flush()
    counts.duplicates = flag_duplicates(session, source, to_check, fuzzy_threshold)
    return counts


def _copy(row: ListingRow, item: Listing, fingerprint: str) -> None:
    row.title, row.price, row.currency = item.title, item.price, item.currency
    row.url, row.extra, row.fingerprint = item.url, item.extra, fingerprint


def recent_counts(session: Session, source: str, window: int) -> list[int]:
    """Item counts of the last ``window`` good, complete runs of ``source`` (newest first)."""
    return list(
        session.scalars(
            select(SourceRunRow.items_found)
            .where(
                SourceRunRow.source == source,
                SourceRunRow.status == "ok",
                SourceRunRow.complete.is_(True),
            )
            .order_by(SourceRunRow.id.desc())
            .limit(window)
        )
    )


def listings_query(
    *,
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    include_duplicates: bool = False,
) -> Select:
    stmt = select(ListingRow)
    if status:
        stmt = stmt.where(ListingRow.status == status)
    if source:
        stmt = stmt.where(ListingRow.source == source)
    if q:
        stmt = stmt.where(func.lower(ListingRow.title).contains(q.lower(), autoescape=True))
    if not include_duplicates:
        stmt = stmt.where(ListingRow.duplicate_of.is_(None))
    return stmt.order_by(ListingRow.source, ListingRow.id)


def search_listings(
    session: Session, *, limit: int | None = None, offset: int = 0, **filters
) -> tuple[int, list[Listing]]:
    stmt = listings_query(**filters)
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    return total, [Listing.model_validate(r) for r in session.scalars(stmt)]
