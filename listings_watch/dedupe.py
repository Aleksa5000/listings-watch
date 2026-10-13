"""Cross-source near-duplicate detection (the same item listed on two different sites)."""

from __future__ import annotations

import re

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from listings_watch.db import ListingRow


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse spaces: 'The  Book!' and 'the book' compare equal."""
    return " ".join(re.sub(r"[\W_]+", " ", text.lower()).split())


def find_duplicate(
    title: str, candidates: dict[int, str], threshold: float
) -> tuple[int, float] | None:
    """Best match for ``title`` among ``{id: normalized title}`` at or above ``threshold`` (0-100).

    token_sort_ratio ignores word order but, unlike token_set_ratio, does not call a short
    title a duplicate of any longer title that merely contains its words.
    """
    query = normalize(title)
    if not query:
        return None
    hit = process.extractOne(
        query, candidates, scorer=fuzz.token_sort_ratio, score_cutoff=threshold
    )
    if hit is None:
        return None
    _, score, listing_id = hit
    return listing_id, round(score, 1)


def flag_duplicates(session: Session, source: str, rows: list[ListingRow], threshold: float) -> int:
    """Point each row at an earlier listing from another source with a near-identical title."""
    rows = [r for r in rows if r.duplicate_of is None]
    if not rows:
        return 0
    others = session.execute(
        select(ListingRow.id, ListingRow.title).where(
            ListingRow.source != source,
            ListingRow.status == "active",
            ListingRow.duplicate_of.is_(None),
        )
    ).all()
    candidates = {i: normalize(t) for i, t in others}
    flagged = 0
    for row in rows:
        match = find_duplicate(row.title, candidates, threshold)
        if match:
            row.duplicate_of, row.duplicate_score = match
            flagged += 1
    return flagged
