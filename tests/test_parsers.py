import pytest

from helpers import fixture
from listings_watch.sources import BooksSource, QuotesJsSource, QuotesSource
from listings_watch.sources.books import parse_price

BOOKS_URL = "https://books.toscrape.com/catalogue/page-1.html"


def test_books_page_parses_all_cards():
    page = BooksSource().parse_page(fixture("books_page1.html"), BOOKS_URL)
    assert len(page.items) == 20 and page.skipped == 0
    first = page.items[0]
    assert first.source == "books"
    assert first.external_id == "a-light-in-the-attic_1000"
    assert first.title == "A Light in the Attic"
    assert (first.price, first.currency) == (51.77, "GBP")
    assert first.url == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    assert first.extra == {"rating": 3, "availability": "In stock"}
    assert len({i.external_id for i in page.items}) == 20


def test_books_pagination_link_is_absolute_and_last_page_has_none():
    source = BooksSource()
    assert source.parse_page(fixture("books_page1.html"), BOOKS_URL).next_url == (
        "https://books.toscrape.com/catalogue/page-2.html"
    )
    last = source.parse_page(fixture("books_page50.html"), BOOKS_URL.replace("-1", "-50"))
    assert len(last.items) == 20 and last.next_url is None


def test_books_unreadable_cards_are_skipped_and_counted():
    html = fixture("books_page1.html").replace('<p class="price_color">', '<p class="oops">', 3)
    page = BooksSource().parse_page(html, BOOKS_URL)
    assert (len(page.items), page.skipped) == (17, 3)


def test_books_layout_change_yields_nothing_rather_than_garbage():
    html = fixture("books_page1.html").replace("product_pod", "card")
    page = BooksSource().parse_page(html, BOOKS_URL)
    assert page.items == [] and page.next_url is not None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£51.77", (51.77, "GBP")),
        ("Â£51.77", (51.77, "GBP")),  # mojibake some servers produce
        ("$1,299.00", (1299.0, "USD")),
        ("12", (12.0, None)),
        ("free", None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_static_quotes_page():
    page = QuotesSource().parse_page(fixture("quotes_page1.html"), "https://quotes.toscrape.com/")
    assert len(page.items) == 10 and page.skipped == 0
    first = page.items[0]
    assert first.title.startswith("The world as we have created it")
    assert not first.title.startswith("“")  # curly quotes stripped
    assert first.extra == {
        "author": "Albert Einstein",
        "tags": ["change", "deep-thoughts", "thinking", "world"],
    }
    assert first.price is None
    assert page.next_url == "https://quotes.toscrape.com/page/2/"


def test_js_rendered_page_matches_static_page():
    """The saved DOM is what Chromium produced after the page's JavaScript ran."""
    static = QuotesSource().parse_page(fixture("quotes_page1.html"), "https://quotes.toscrape.com/")
    rendered = QuotesJsSource().parse_page(
        fixture("quotes_js_rendered_page1.html"), "https://quotes.toscrape.com/js/"
    )
    assert len(rendered.items) == 10
    assert rendered.next_url == "https://quotes.toscrape.com/js/page/2/"
    assert [(i.external_id, i.title, i.extra) for i in rendered.items] == [
        (i.external_id, i.title, i.extra) for i in static.items
    ]


def test_js_page_source_html_has_no_quotes_until_javascript_runs():
    """Why a plain HTTP fetch is not enough for this site."""
    raw = fixture("site/js/index.html")
    assert QuotesJsSource().parse_page(raw, "https://quotes.toscrape.com/js/").items == []
