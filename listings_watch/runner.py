"""One pipeline run: scrape each source, health-check it, store it, alert on trouble."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import sessionmaker

from listings_watch.alerts import send_alert
from listings_watch.config import Settings
from listings_watch.db import RunRow, SourceRunRow, utcnow
from listings_watch.health import check_health
from listings_watch.sources.base import Source
from listings_watch.store import Counts, apply_scrape, recent_counts, unique_items

log = logging.getLogger("listings_watch")


@dataclass
class SourceOutcome:
    source: str
    status: str  # ok | broken | failed
    counts: Counts = field(default_factory=Counts)
    items_found: int = 0
    pages: int = 0
    complete: bool = True
    skipped: int = 0
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class RunSummary:
    run_id: int
    outcomes: list[SourceOutcome]
    alert_sent: bool = False

    @property
    def problems(self) -> list[SourceOutcome]:
        return [o for o in self.outcomes if o.status != "ok"]

    @property
    def exit_code(self) -> int:
        return 1 if self.problems else 0


async def run_pipeline(
    settings: Settings,
    sessions: sessionmaker,
    sources: list[Source],
    *,
    notify: Callable[[str | None, str], bool] = send_alert,
) -> RunSummary:
    with sessions() as session:
        run = RunRow(started_at=utcnow())
        session.add(run)
        session.commit()
        run_id = run.id

    outcomes = []
    for source in sources:
        outcome = await _run_source(settings, sessions, source, run_id)
        outcomes.append(outcome)

    with sessions() as session:
        session.get(RunRow, run_id).finished_at = utcnow()
        session.commit()

    summary = RunSummary(run_id, outcomes)
    if summary.problems and settings.alert_webhook_url:
        summary.alert_sent = notify(settings.alert_webhook_url, alert_text(summary, len(sources)))
    return summary


async def _run_source(
    settings: Settings, sessions: sessionmaker, source: Source, run_id: int
) -> SourceOutcome:
    started = time.monotonic()
    outcome = SourceOutcome(source.name, "ok")
    try:
        result = await source.scrape(settings, settings.max_pages)
    except Exception as exc:  # deliberate: one broken source must not stop the others
        outcome.status, outcome.error = "failed", f"{type(exc).__name__}: {exc}"
        log.error("%s failed: %s", source.name, outcome.error)
        result = None

    with sessions() as session:
        if result is not None:
            items = unique_items(result.items)
            outcome.items_found, outcome.pages = len(items), result.pages
            outcome.complete, outcome.skipped = result.complete, result.skipped
            health = check_health(
                len(items),
                recent_counts(session, source.name, settings.health_window),
                complete=result.complete,
                drop_threshold=settings.drop_threshold,
            )
            if health.ok:
                outcome.counts = apply_scrape(
                    session,
                    source.name,
                    items,
                    complete=result.complete,
                    removed_after=settings.removed_after,
                    fuzzy_threshold=settings.fuzzy_threshold,
                )
            else:  # broken: store nothing, remove nothing, keep the last good data untouched
                outcome.status, outcome.error = "broken", health.reason
                log.error("%s looks broken: %s", source.name, health.reason)
        outcome.duration_s = round(time.monotonic() - started, 1)
        c = outcome.counts
        session.add(
            SourceRunRow(
                run_id=run_id,
                source=source.name,
                status=outcome.status,
                items_found=outcome.items_found,
                new=c.new,
                updated=c.updated,
                unchanged=c.unchanged,
                removed=c.removed,
                duplicates=c.duplicates,
                skipped=outcome.skipped,
                pages=outcome.pages,
                complete=outcome.complete,
                error=outcome.error,
                duration_s=outcome.duration_s,
            )
        )
        session.commit()
    return outcome


def alert_text(summary: RunSummary, total_sources: int) -> str:
    lines = [
        f"listings-watch: {len(summary.problems)} of {total_sources} source(s) need attention "
        f"(run #{summary.run_id})"
    ]
    lines += [f"- {o.source}: {o.status.upper()} - {o.error}" for o in summary.problems]
    lines.append("Nothing was marked removed for these sources; their last good data is untouched.")
    return "\n".join(lines)
