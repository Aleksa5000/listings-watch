"""Command line: run, report, export, serve."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import func, select

from listings_watch.config import Settings
from listings_watch.db import ListingRow, RunRow, SourceRunRow, make_engine, session_factory
from listings_watch.export import to_csv, to_xlsx
from listings_watch.runner import RunSummary, run_pipeline
from listings_watch.sources import SOURCES, get_sources
from listings_watch.store import search_listings


class Format(StrEnum):
    csv = "csv"
    xlsx = "xlsx"


class Status(StrEnum):
    active = "active"
    removed = "removed"


app = typer.Typer(
    help="Scrape several sites into one clean listings database.",
    no_args_is_help=True,
    add_completion=False,
)


def _settings(ctx: typer.Context, **overrides) -> Settings:
    settings: Settings = ctx.obj
    overrides = {k: v for k, v in overrides.items() if v is not None}
    return replace(settings, **overrides) if overrides else settings


@app.callback()
def main(
    ctx: typer.Context,
    db: Annotated[
        str | None, typer.Option("--db", help="SQLite file or SQLAlchemy URL (env LW_DB_URL).")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log every page.")] = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    ctx.obj = replace(settings, db_url=db) if db else settings


def _print_summary(summary: RunSummary) -> None:
    typer.echo(f"Run #{summary.run_id}")
    for o in summary.outcomes:
        c = o.counts
        detail = (
            f"found {o.items_found:>4}  new {c.new:>4}  updated {c.updated:>3}  "
            f"unchanged {c.unchanged:>4}  removed {c.removed:>3}  dupes {c.duplicates:>3}  "
            f"pages {o.pages:>2}  {o.duration_s:>5.1f}s"
        )
        typer.echo(
            f"  {o.source:<10} {o.status.upper():<6} {detail if o.status == 'ok' else o.error}"
        )
        if o.status == "ok" and not o.complete:
            typer.echo(f"  {'':<10} (stopped at max pages: nothing marked removed)")
    if summary.problems:
        sent = (
            "alert sent" if summary.alert_sent else "no alert (ALERT_WEBHOOK_URL unset or failed)"
        )
        typer.echo(f"{len(summary.problems)} source(s) need attention; {sent}.", err=True)
    else:
        typer.echo("All sources healthy.")


@app.command()
def run(
    ctx: typer.Context,
    source: Annotated[
        list[str] | None, typer.Option("--source", "-s", help=f"Source(s): {', '.join(SOURCES)}.")
    ] = None,
    max_pages: Annotated[
        int | None, typer.Option(help="Stop after N pages per source (0 = all).")
    ] = None,
    delay: Annotated[float | None, typer.Option(help="Seconds between requests.")] = None,
) -> None:
    """Scrape the sources, update the database, alert if one is broken. Exit code 1 on trouble."""
    settings = _settings(ctx, max_pages=max_pages, delay=delay)
    try:
        sources = get_sources(source)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    sessions = session_factory(make_engine(settings.db_url))
    summary = asyncio.run(run_pipeline(settings, sessions, sources))
    _print_summary(summary)
    raise typer.Exit(summary.exit_code)


@app.command()
def report(ctx: typer.Context) -> None:
    """Show the latest run per source and what is in the database."""
    sessions = session_factory(make_engine(_settings(ctx).db_url))
    with sessions() as s:
        run_row = s.scalars(select(RunRow).order_by(RunRow.id.desc())).first()
        if run_row is None:
            typer.echo("No runs yet. Start with: listings-watch run")
            return
        typer.echo(f"Latest run #{run_row.id} at {run_row.started_at:%Y-%m-%d %H:%M} UTC")
        for r in s.scalars(select(SourceRunRow).where(SourceRunRow.run_id == run_row.id)):
            typer.echo(
                f"  {r.source:<10} {r.status.upper():<6} found {r.items_found}  new {r.new}  "
                f"updated {r.updated}  unchanged {r.unchanged}  removed {r.removed}"
                + (f"  -> {r.error}" if r.error else "")
            )
        typer.echo("Database:")
        rows = s.execute(
            select(ListingRow.source, ListingRow.status, func.count())
            .group_by(ListingRow.source, ListingRow.status)
            .order_by(ListingRow.source, ListingRow.status)
        )
        for name, status, n in rows:
            typer.echo(f"  {name:<10} {status:<8} {n}")
        dupes = s.scalar(
            select(func.count()).select_from(ListingRow).where(ListingRow.duplicate_of.is_not(None))
        )
        typer.echo(f"  cross-source duplicates flagged: {dupes}")


@app.command()
def export(
    ctx: typer.Context,
    format: Annotated[Format, typer.Option("--format", "-f")] = Format.csv,
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Default: exports/listings.<fmt>")
    ] = None,
    status: Annotated[Status | None, typer.Option()] = None,
    source: Annotated[str | None, typer.Option()] = None,
    include_duplicates: Annotated[bool, typer.Option(help="Keep cross-source duplicates.")] = False,
) -> None:
    """Write listings to a CSV or Excel file."""
    sessions = session_factory(make_engine(_settings(ctx).db_url))
    with sessions() as s:
        _, items = search_listings(
            s,
            status=status.value if status else None,
            source=source,
            include_duplicates=include_duplicates,
        )
    out = out or Path("exports") / f"listings.{format.value}"
    out.parent.mkdir(parents=True, exist_ok=True)
    if format is Format.csv:
        out.write_text(to_csv(items), encoding="utf-8", newline="")
    else:
        out.write_bytes(to_xlsx(items))
    typer.echo(f"Exported {len(items)} rows to {out}")


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Bind address. No auth, keep it local.")] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8080,
) -> None:
    """Serve the read-only API: /listings, /listings.csv, /listings.xlsx, /runs, /health."""
    import uvicorn

    from listings_watch.api import create_app

    settings = _settings(ctx)
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
