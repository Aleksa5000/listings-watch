"""Decide whether a scrape result can be trusted before it touches the database."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class Health:
    ok: bool
    reason: str | None = None


def check_health(
    count: int,
    history: list[int],
    *,
    complete: bool = True,
    drop_threshold: float = 0.5,
) -> Health:
    """A source is broken when it returns nothing, or far fewer items than its recent average.

    ``history`` holds item counts of recent good, complete runs. A run capped by ``max_pages``
    (``complete=False``) is expected to be smaller, so only the zero check applies to it.
    """
    if count == 0:
        return Health(False, "returned 0 items (site down, blocked, or layout changed)")
    if complete and history:
        average = mean(history)
        if count < average * (1 - drop_threshold):
            drop = 100 * (1 - count / average)
            return Health(
                False,
                f"returned {count} items, {drop:.0f}% below the recent average of {average:.0f} "
                f"(last {len(history)} good runs)",
            )
    return Health(True)
