"""The JavaScript path, end to end and offline: real headless Chromium against a local copy of
the /js/ demo pages. Skipped (not failed) if Chromium is not installed:
    python -m playwright install chromium
"""

import asyncio
import functools
import http.server
import threading

import pytest

from helpers import FIXTURES
from listings_watch.fetch import FetchError
from listings_watch.sources import QuotesJsSource


class Quiet(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
    }

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def local_site():
    handler = functools.partial(Quiet, directory=str(FIXTURES / "site"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def chromium_or_skip(settings):
    from playwright.async_api import async_playwright

    async def probe():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            await browser.close()

    try:
        asyncio.run(probe())
    except Exception as exc:
        pytest.skip(f"Chromium not available: {str(exc).splitlines()[0]}")


def local_source(base: str):
    return type("LocalQuotesJs", (QuotesJsSource,), {"start_url": f"{base}/js/"})()


def test_browser_renders_javascript_and_follows_pagination(local_site, settings, chromium_or_skip):
    result = asyncio.run(local_source(local_site).scrape(settings, max_pages=2))
    assert (result.pages, len(result.items), result.complete is False) == (2, 20, True)
    first = result.items[0]
    assert first.title.startswith("The world as we have created it")
    assert first.extra["author"] == "Albert Einstein" and first.url == f"{local_site}/js/"
    assert result.items[10].url == f"{local_site}/js/page/2/"
    assert len({i.external_id for i in result.items}) == 20


def test_browser_page_that_never_renders_fails_instead_of_returning_nothing(
    local_site, settings, chromium_or_skip
):
    from dataclasses import replace

    class NoQuotes(QuotesJsSource):
        start_url = f"{local_site}/static/jquery.js"  # a page without any div.quote

    fast = replace(settings, timeout=1.5, attempts=1)
    with pytest.raises(FetchError):
        asyncio.run(NoQuotes().scrape(fast))
