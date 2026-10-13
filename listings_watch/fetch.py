"""Polite fetching: robots.txt, a custom User-Agent, a delay between requests, retries with backoff.

Two fetchers share the same rules: ``Fetcher`` (plain HTTP via httpx) and ``BrowserFetcher``
(headless Chromium via Playwright, for pages that only show their content after JavaScript runs).
There is no proxy rotation and no anti-bot evasion on purpose.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from listings_watch.config import Settings

log = logging.getLogger("listings_watch")
T = TypeVar("T")

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 30.0


class FetchError(Exception):
    """A page could not be fetched (after retries)."""


class RobotsDisallowed(FetchError):
    """robots.txt forbids this URL for our User-Agent, so we do not fetch it."""


class Retryable(Exception):
    def __init__(self, message: str, wait: float | None = None):
        super().__init__(message)
        self.wait = wait


class _Polite:
    """Pacing and retry logic shared by both fetchers."""

    def __init__(self, settings: Settings, sleep=asyncio.sleep, clock=time.monotonic):
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    async def _pace(self) -> None:
        if self._last_request is not None:
            wait = self.settings.delay - (self._clock() - self._last_request)
            if wait > 0:
                await self._sleep(wait)
        self._last_request = self._clock()

    async def _retrying(self, what: str, attempt: Callable[[], Awaitable[T]]) -> T:
        last = "no attempt made"
        for n in range(self.settings.attempts):
            await self._pace()
            try:
                return await attempt()
            except Retryable as exc:
                last = str(exc)
                if n + 1 < self.settings.attempts:
                    wait = exc.wait if exc.wait is not None else self.settings.backoff * 2**n
                    log.warning("%s: %s; retry %d in %.1fs", what, last, n + 1, wait)
                    await self._sleep(wait)
        raise FetchError(f"{what}: gave up after {self.settings.attempts} attempts ({last})")


class Fetcher(_Polite):
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        super().__init__(settings, sleep, clock)
        self._transport = transport
        self._robots: dict[str, RobotFileParser] = {}

    async def __aenter__(self) -> Fetcher:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.timeout,
            follow_redirects=True,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _get(self, url: str) -> httpx.Response:
        async def attempt() -> httpx.Response:
            try:
                response = await self._client.get(url)
            except httpx.TransportError as exc:  # timeouts and connection errors
                raise Retryable(f"{type(exc).__name__}: {exc}") from exc
            if response.status_code in RETRY_STATUS:
                header = response.headers.get("Retry-After", "")
                wait = min(float(header), MAX_RETRY_AFTER) if header.isdigit() else None
                raise Retryable(f"HTTP {response.status_code}", wait)
            return response

        return await self._retrying(url, attempt)

    async def allowed(self, url: str) -> bool:
        """robots.txt check, cached per site. No robots.txt (404) means everything is allowed."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            response = await self._get(f"{origin}/robots.txt")
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            elif response.status_code in (401, 403):
                parser.disallow_all = True
            else:
                parser.allow_all = True
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(self.settings.user_agent, url)

    async def __call__(self, url: str) -> str:
        if not await self.allowed(url):
            raise RobotsDisallowed(f"{url} is disallowed by robots.txt")
        response = await self._get(url)
        if response.status_code >= 400:
            raise FetchError(f"{url}: HTTP {response.status_code}")
        return response.text


class BrowserFetcher(_Polite):
    """Loads a page in headless Chromium, waits for ``ready_selector``, returns rendered HTML."""

    def __init__(self, settings: Settings, ready_selector: str, *, sleep=asyncio.sleep):
        super().__init__(settings, sleep)
        self.ready_selector = ready_selector
        self._robots = Fetcher(settings, sleep=sleep)

    async def __aenter__(self) -> BrowserFetcher:
        from playwright.async_api import async_playwright  # imported late: HTTP-only runs skip it

        await self._robots.__aenter__()
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=True)
        except Exception:
            await self._pw.stop()
            await self._robots.__aexit__()
            raise
        context = await self._browser.new_context(user_agent=self.settings.user_agent)
        self._page = await context.new_page()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._browser.close()
        await self._pw.stop()
        await self._robots.__aexit__()

    async def __call__(self, url: str) -> str:
        from playwright.async_api import Error as PlaywrightError

        if not await self._robots.allowed(url):
            raise RobotsDisallowed(f"{url} is disallowed by robots.txt")
        timeout_ms = self.settings.timeout * 1000

        async def attempt() -> str:
            try:
                response = await self._page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                if response is not None and response.status in RETRY_STATUS:
                    raise Retryable(f"HTTP {response.status}")
                if response is not None and response.status >= 400:
                    raise FetchError(f"{url}: HTTP {response.status}")
                await self._page.wait_for_selector(self.ready_selector, timeout=timeout_ms)
                return await self._page.content()
            except PlaywrightError as exc:  # includes Playwright's TimeoutError
                raise Retryable(f"{type(exc).__name__}: {str(exc).splitlines()[0]}") from exc

        return await self._retrying(url, attempt)
