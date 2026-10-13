"""Source plugin interface.

A source is a small class: where to start (``start_url``), how to turn one page of HTML into
listings plus the link to the next page (``parse_page``), and how to fetch (plain HTTP or a
real browser). Pagination, politeness and retries are handled here, once, for every source.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

import httpx

from listings_watch.config import Settings
from listings_watch.fetch import BrowserFetcher, Fetcher
from listings_watch.models import Listing

log = logging.getLogger("listings_watch")
HARD_PAGE_LIMIT = 1000  # safety net against pagination loops


@dataclass
class ParsedPage:
    items: list[Listing]
    next_url: str | None = None
    skipped: int = 0  # cards that were on the page but could not be read (layout change?)


@dataclass
class ScrapeResult:
    items: list[Listing] = field(default_factory=list)
    pages: int = 0
    complete: bool = True  # False when we stopped before the last page (max_pages)
    skipped: int = 0


class Fetch(Protocol):
    async def __call__(self, url: str) -> str: ...


class Source(ABC):
    name: ClassVar[str]
    start_url: ClassVar[str]

    @abstractmethod
    def parse_page(self, html: str, url: str) -> ParsedPage:
        """Pure function: HTML in, listings and next-page URL out. Easy to test on saved HTML."""

    @abstractmethod
    def open(self, settings: Settings) -> AbstractAsyncContextManager[Fetch]:
        """An async context manager that yields ``await fetch(url) -> html``."""

    async def scrape(self, settings: Settings, max_pages: int = 0) -> ScrapeResult:
        result = ScrapeResult()
        url: str | None = self.start_url
        visited: set[str] = set()
        async with self.open(settings) as fetch:
            while url:
                if url in visited:
                    log.warning("%s: pagination loop at %s, stopping", self.name, url)
                    result.complete = False
                    break
                if (max_pages and result.pages >= max_pages) or result.pages >= HARD_PAGE_LIMIT:
                    result.complete = False
                    break
                visited.add(url)
                parsed = self.parse_page(await fetch(url), url)
                result.pages += 1
                result.items += parsed.items
                result.skipped += parsed.skipped
                if parsed.skipped:
                    log.warning(
                        "%s: skipped %d unreadable item(s) on %s", self.name, parsed.skipped, url
                    )
                log.info("%s: page %d -> %d items", self.name, result.pages, len(parsed.items))
                url = parsed.next_url
        return result


class HttpSource(Source):
    """Static HTML fetched with httpx."""

    transport: httpx.AsyncBaseTransport | None = None  # tests inject a mock transport here

    def open(self, settings: Settings) -> AbstractAsyncContextManager[Fetch]:
        return Fetcher(settings, transport=self.transport)


class BrowserSource(Source):
    """JavaScript-rendered pages: loaded in headless Chromium, parsed after rendering."""

    ready_selector: ClassVar[str]  # element that exists only once the page has rendered

    def open(self, settings: Settings) -> AbstractAsyncContextManager[Fetch]:
        return BrowserFetcher(settings, self.ready_selector)
