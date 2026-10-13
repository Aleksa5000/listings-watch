import pytest
from sqlalchemy import select

from helpers import make
from listings_watch.db import ListingRow
from listings_watch.dedupe import find_duplicate, normalize
from listings_watch.models import Listing
from listings_watch.store import apply_scrape, unique_items


def test_normalize_ignores_case_punctuation_and_spacing():
    assert normalize("  The  Book!! (2nd ed.) ") == "the book 2nd ed"


def test_find_duplicate_exact_reordered_and_punctuation():
    candidates = {1: normalize("Sharp Objects"), 2: normalize("The Great Gatsby")}
    assert find_duplicate("Sharp Objects", candidates, 92) == (1, 100.0)
    assert find_duplicate("SHARP objects!", candidates, 92)[0] == 1
    assert find_duplicate("Gatsby, The Great", candidates, 92)[0] == 2  # word order ignored


def test_find_duplicate_rejects_different_titles():
    candidates = {1: normalize("Sharp Objects")}
    assert find_duplicate("Dark Places", candidates, 92) is None
    assert find_duplicate("!!!", candidates, 0) is None  # nothing left after normalising


def test_threshold_is_configurable():
    candidates = {1: normalize("Harry Potter and the Sorcerer's Stone")}
    near = "Harry Potter and the Sorcerers Stone (Book 1)"
    assert find_duplicate(near, candidates, 99) is None
    assert find_duplicate(near, candidates, 80)[0] == 1


def test_unique_items_keeps_first_of_same_key():
    a, b = make("s", 1, title="first"), make("s", 1, title="second")
    assert [i.title for i in unique_items([a, b, make("s", 2)])] == ["first", "Item number 2"]
    # same external id on a different source is a different listing
    assert len(unique_items([make("s", 1), make("t", 1)])) == 2


def _store(session, source, items, threshold=92):
    result = apply_scrape(
        session, source, items, complete=True, removed_after=2, fuzzy_threshold=threshold
    )
    session.commit()
    return result


def test_cross_source_duplicate_is_flagged_on_the_later_source(sessions):
    shop_a = [make("a", 1, title="Blue Widget Pro"), make("a", 2, title="Red Gadget")]
    shop_b = [
        make("b", 7, title="Blue widget pro"),  # same item, different case
        make("b", 8, title="Green Thing"),
    ]
    with sessions() as s:
        assert _store(s, "a", shop_a).duplicates == 0
        counts = _store(s, "b", shop_b)
        assert (counts.new, counts.duplicates) == (2, 1)
        rows = {r.title: r for r in s.scalars(select(ListingRow))}
        dup, original = rows["Blue widget pro"], rows["Blue Widget Pro"]
        assert dup.duplicate_of == original.id and dup.duplicate_score >= 92
        assert original.duplicate_of is None and rows["Green Thing"].duplicate_of is None


def test_same_title_inside_one_source_is_not_a_cross_source_duplicate(sessions):
    items = [make("a", 1, title="Same Title"), make("a", 2, title="Same Title")]
    with sessions() as s:
        assert _store(s, "a", items).duplicates == 0


@pytest.mark.parametrize(("threshold", "flagged"), [(99, 0), (80, 1)])
def test_threshold_controls_what_gets_flagged(sessions, threshold, flagged):
    with sessions() as s:
        _store(s, "a", [make("a", 1, title="Wireless Mouse M185 Black")], threshold)
        counts = _store(s, "b", [make("b", 1, title="Wireless Mouse M185 (Black)!!x")], threshold)
        assert counts.duplicates == flagged


def test_listing_model_validates():
    with pytest.raises(ValueError):
        Listing(source="s", external_id="1", title="   ", url="u")
    with pytest.raises(ValueError):
        Listing(source="s", external_id="1", title="t", url="u", price=-1)
