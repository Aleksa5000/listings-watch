"""Registry of available sources. To add one, write a Source subclass and list it here."""

from listings_watch.sources.base import BrowserSource, HttpSource, ParsedPage, ScrapeResult, Source
from listings_watch.sources.books import BooksSource
from listings_watch.sources.quotes import QuotesJsSource, QuotesSource

SOURCES: dict[str, type[Source]] = {
    cls.name: cls for cls in (BooksSource, QuotesSource, QuotesJsSource)
}


def get_sources(names: list[str] | None = None) -> list[Source]:
    names = names or list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        raise ValueError(
            f"unknown source(s): {', '.join(unknown)} (available: {', '.join(SOURCES)})"
        )
    return [SOURCES[n]() for n in names]


__all__ = [
    "SOURCES",
    "BrowserSource",
    "HttpSource",
    "ParsedPage",
    "ScrapeResult",
    "Source",
    "get_sources",
]
