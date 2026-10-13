"""Shared test helpers: listing factory, fake sources, fixture reader."""

from pathlib import Path

from listings_watch.models import Listing
from listings_watch.sources.base import ScrapeResult, Source

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make(source="shop", n=1, title=None, price=10.0, **kw) -> Listing:
    return Listing(
        source=source,
        external_id=kw.pop("external_id", f"id-{n}"),
        title=title or f"Item number {n}",
        price=price,
        currency="EUR",
        url=f"https://example.test/{source}/{n}",
        **kw,
    )


class FakeSource(Source):
    """A source whose output the test controls: set .items, .error or .complete between runs."""

    start_url = "https://example.test/"

    def __init__(self, name="shop", items=None):
        self.name = name
        self.items = items if items is not None else []
        self.error: Exception | None = None
        self.complete = True

    def parse_page(self, html, url):  # not used: scrape() is overridden
        raise NotImplementedError

    def open(self, settings):
        raise NotImplementedError

    async def scrape(self, settings, max_pages=0):
        if self.error:
            raise self.error
        return ScrapeResult(list(self.items), pages=1, complete=self.complete)


def items_for(source: str, count: int, **kw) -> list[Listing]:
    return [make(source, n, **kw) for n in range(1, count + 1)]
