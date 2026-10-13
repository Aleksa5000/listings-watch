from datetime import datetime, timedelta

from sqlalchemy import select

from helpers import items_for, make
from listings_watch.db import ListingRow, RunRow, SourceRunRow, utcnow
from listings_watch.store import apply_scrape, recent_counts, search_listings

T0 = datetime(2026, 1, 1, 6, 0)


def scrape(session, items, *, source="shop", day=0, complete=True, removed_after=2):
    counts = apply_scrape(
        session,
        source,
        items,
        complete=complete,
        removed_after=removed_after,
        fuzzy_threshold=92,
        now=T0 + timedelta(days=day),
    )
    session.commit()
    return counts


def row(session, external_id, source="shop"):
    return session.scalars(
        select(ListingRow).where(ListingRow.source == source, ListingRow.external_id == external_id)
    ).one()


def test_first_run_everything_is_new_second_run_unchanged(sessions):
    items = items_for("shop", 3)
    with sessions() as s:
        c1 = scrape(s, items)
        c2 = scrape(s, items, day=1)
        assert (c1.new, c1.unchanged) == (3, 0)
        assert (c2.new, c2.updated, c2.unchanged, c2.removed) == (0, 0, 3, 0)
        r = row(s, "id-1")
        assert r.first_seen == T0 and r.last_seen == T0 + timedelta(days=1)


def test_changed_price_counts_as_updated(sessions):
    with sessions() as s:
        scrape(s, items_for("shop", 2))
        changed = [make("shop", 1, price=12.5), make("shop", 2)]
        counts = scrape(s, changed, day=1)
        assert (counts.updated, counts.unchanged) == (1, 1)
        assert row(s, "id-1").price == 12.5


def test_listing_removed_only_after_n_consecutive_missed_runs(sessions):
    gone = items_for("shop", 3)[:2]  # id-3 disappears
    with sessions() as s:
        scrape(s, items_for("shop", 3))
        c = scrape(s, gone, day=1)
        assert c.removed == 0 and row(s, "id-3").status == "active"
        assert row(s, "id-3").missed_runs == 1
        c = scrape(s, gone, day=2)
        assert c.removed == 1
        r = row(s, "id-3")
        assert r.status == "removed" and r.removed_at == T0 + timedelta(days=2)
        c = scrape(s, gone, day=3)  # already removed: not counted again
        assert c.removed == 0 and row(s, "id-1").status == "active"


def test_a_listing_that_comes_back_resets_the_counter(sessions):
    with sessions() as s:
        scrape(s, items_for("shop", 2))
        scrape(s, items_for("shop", 2)[:1], day=1)  # id-2 missed once
        scrape(s, items_for("shop", 2), day=2)  # seen again
        assert row(s, "id-2").missed_runs == 0
        scrape(s, items_for("shop", 2)[:1], day=3)  # missed once more: not "consecutive"
        assert row(s, "id-2").status == "active"


def test_removed_listing_is_reactivated_when_it_reappears(sessions):
    with sessions() as s:
        scrape(s, items_for("shop", 2))
        scrape(s, items_for("shop", 2)[:1], day=1)
        scrape(s, items_for("shop", 2)[:1], day=2)
        assert row(s, "id-2").status == "removed"
        counts = scrape(s, items_for("shop", 2), day=3)
        r = row(s, "id-2")
        assert (r.status, r.missed_runs, r.removed_at) == ("active", 0, None)
        assert counts.updated == 1 and counts.new == 0


def test_incomplete_run_never_marks_anything_removed(sessions):
    with sessions() as s:
        scrape(s, items_for("shop", 5))
        for day in range(1, 5):  # a run capped by max_pages sees only 2 of 5 items
            c = scrape(s, items_for("shop", 5)[:2], day=day, complete=False)
            assert c.removed == 0
        assert row(s, "id-5").status == "active" and row(s, "id-5").missed_runs == 0


def test_removal_is_per_source(sessions):
    with sessions() as s:
        scrape(s, items_for("a", 2), source="a")
        scrape(s, items_for("b", 2), source="b")
        for day in (1, 2):
            scrape(s, [], source="a", day=day)  # the health check normally stops an empty source
        assert row(s, "id-1", "a").status == "removed"
        assert row(s, "id-1", "b").status == "active"


def test_removing_a_canonical_listing_promotes_its_duplicates(sessions):
    with sessions() as s:
        scrape(s, [make("a", 1, title="Blue Widget Pro")], source="a")
        scrape(s, [make("b", 1, title="Blue Widget Pro")], source="b")
        assert row(s, "id-1", "b").duplicate_of == row(s, "id-1", "a").id
        for day in (1, 2):  # source a drops it, b still has it
            scrape(s, [make("a", 2, title="Other thing")], source="a", day=day)
            scrape(s, [make("b", 1, title="Blue Widget Pro")], source="b", day=day)
        assert row(s, "id-1", "a").status == "removed"
        promoted = row(s, "id-1", "b")
        assert promoted.duplicate_of is None and promoted.status == "active"


def test_search_filters(sessions):
    with sessions() as s:
        scrape(
            s,
            [make("a", 1, title="Blue Widget"), make("a", 2, title="Red 100% Gadget")],
            source="a",
        )
        scrape(s, [make("b", 1, title="blue widget")], source="b")  # duplicate of a/1
        assert search_listings(s)[0] == 2  # duplicates hidden by default
        assert search_listings(s, include_duplicates=True)[0] == 3
        assert search_listings(s, source="b")[0] == 0
        assert search_listings(s, source="b", include_duplicates=True)[0] == 1
        assert [i.title for i in search_listings(s, q="GADGET")[1]] == ["Red 100% Gadget"]
        assert search_listings(s, q="%")[0] == 1  # % is a literal, not a wildcard
        scrape(s, [], source="a", day=1)
        scrape(s, [], source="a", day=2)
        assert search_listings(s, status="removed")[0] == 2
        assert search_listings(s, status="active", include_duplicates=True)[0] == 1
        total, page = search_listings(s, limit=1, offset=1, include_duplicates=True)
        assert total == 3 and len(page) == 1


def test_recent_counts_only_use_good_complete_runs(sessions):
    with sessions() as s:
        s.add(RunRow(id=1, started_at=utcnow()))
        for status, found, complete in [
            ("ok", 100, True),
            ("broken", 0, True),
            ("ok", 20, False),
            ("ok", 90, True),
            ("failed", 0, True),
        ]:
            s.add(
                SourceRunRow(
                    run_id=1, source="x", status=status, items_found=found, complete=complete
                )
            )
        s.add(SourceRunRow(run_id=1, source="other", status="ok", items_found=5, complete=True))
        s.commit()
        assert recent_counts(s, "x", 5) == [90, 100]
        assert recent_counts(s, "x", 1) == [90]
