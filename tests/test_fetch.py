"""Polite fetching, retries, robots.txt and pagination, all against a mocked transport."""

import asyncio
from dataclasses import replace

import httpx
import pytest

from helpers import fixture
from listings_watch.fetch import Fetcher, FetchError, RobotsDisallowed
from listings_watch.sources import BooksSource


class Site:
    """A fake website: path -> list of responses served in order (the last one repeats)."""

    def __init__(self, routes):
        self.routes = {p: list(r) for p, r in routes.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get(request.url.path)
        if queue is None:
            return httpx.Response(404)
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        return (
            response if isinstance(response, httpx.Response) else httpx.Response(200, text=response)
        )

    @property
    def paths(self):
        return [r.url.path for r in self.requests]


def fetch_all(settings, site, urls, **kw):
    async def go():
        async with Fetcher(settings, transport=httpx.MockTransport(site), **kw) as fetch:
            return [await fetch(u) for u in urls]

    return asyncio.run(go())


def test_sends_custom_user_agent(settings):
    site = Site({"/a": ["ok"]})
    fetch_all(settings, site, ["https://x.test/a"])
    assert {r.headers["user-agent"] for r in site.requests} == {settings.user_agent}
    assert "listings-watch" in settings.user_agent


def test_retries_server_errors_with_exponential_backoff(settings):
    site = Site({"/a": [httpx.Response(503), httpx.Response(502), "finally"]})
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    s = replace(settings, backoff=2.0)
    assert fetch_all(s, site, ["https://x.test/a"], sleep=fake_sleep) == ["finally"]
    assert sleeps == [2.0, 4.0]  # 2s, then 4s


def test_retry_after_header_is_honoured_and_capped(settings):
    site = Site({"/a": [httpx.Response(429, headers={"Retry-After": "7"}), "ok"]})
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    fetch_all(settings, site, ["https://x.test/a"], sleep=fake_sleep)
    assert sleeps == [7.0]


def test_gives_up_after_configured_attempts(settings):
    site = Site({"/a": [httpx.Response(500)]})
    with pytest.raises(FetchError, match="gave up after 3 attempts"):
        fetch_all(settings, site, ["https://x.test/a"])
    assert site.paths.count("/a") == 3


def test_connection_errors_are_retried_too(settings):
    calls = []

    def flaky(request):
        calls.append(request.url.path)
        if request.url.path == "/a" and calls.count("/a") == 1:
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(200, text="ok")

    async def go():
        async with Fetcher(settings, transport=httpx.MockTransport(flaky)) as fetch:
            return await fetch("https://x.test/a")

    assert asyncio.run(go()) == "ok" and calls.count("/a") == 2


def test_client_errors_are_not_retried(settings):
    site = Site({"/missing": [httpx.Response(404)]})
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_all(settings, site, ["https://x.test/missing"])
    assert site.paths.count("/missing") == 1


def test_robots_txt_disallow_blocks_the_page_and_is_fetched_once(settings):
    robots = "User-agent: *\nDisallow: /private/\n"
    site = Site({"/robots.txt": [robots], "/public/1": ["ok"], "/private/1": ["secret"]})
    with pytest.raises(RobotsDisallowed):
        fetch_all(settings, site, ["https://x.test/public/1", "https://x.test/private/1"])
    assert "/private/1" not in site.paths
    assert site.paths.count("/robots.txt") == 1


def test_robots_rules_for_our_own_user_agent_apply(settings):
    robots = "User-agent: listings-watch\nDisallow: /\n"
    site = Site({"/robots.txt": [robots], "/a": ["ok"]})
    with pytest.raises(RobotsDisallowed):
        fetch_all(settings, site, ["https://x.test/a"])


def test_missing_robots_txt_means_allowed_and_forbidden_means_blocked(settings):
    assert fetch_all(settings, Site({"/a": ["ok"]}), ["https://x.test/a"]) == ["ok"]
    site = Site({"/robots.txt": [httpx.Response(403)], "/a": ["ok"]})
    with pytest.raises(RobotsDisallowed):
        fetch_all(settings, site, ["https://x.test/a"])


def test_delay_between_requests_is_enforced(settings):
    site = Site({"/a": ["1"], "/b": ["2"]})
    now, sleeps = [100.0], []

    async def fake_sleep(seconds):
        sleeps.append(round(seconds, 2))
        now[0] += seconds

    s = replace(settings, delay=1.5)
    fetch_all(
        s, site, ["https://x.test/a", "https://x.test/b"], sleep=fake_sleep, clock=lambda: now[0]
    )
    # robots.txt, /a and /b are three requests: every gap is padded up to the delay
    assert sleeps == [1.5, 1.5]


# --- a whole crawl over the saved pages ---------------------------------------------------


def books_site():
    page1, last = fixture("books_page1.html"), fixture("books_page50.html")
    return Site({"/catalogue/page-1.html": [page1], "/catalogue/page-2.html": [last]})


def crawl(settings, site, max_pages=0):
    source = BooksSource()
    source.transport = httpx.MockTransport(site)
    return asyncio.run(source.scrape(settings, max_pages))


def test_crawl_follows_pagination_until_the_last_page(settings):
    site = books_site()
    result = crawl(settings, site)
    assert (result.pages, len(result.items), result.complete) == (2, 40, True)
    assert site.paths == ["/robots.txt", "/catalogue/page-1.html", "/catalogue/page-2.html"]


def test_crawl_stopped_by_max_pages_is_marked_incomplete(settings):
    result = crawl(settings, books_site(), max_pages=1)
    assert (result.pages, len(result.items), result.complete) == (1, 20, False)


def test_crawl_survives_a_flaky_page_but_fails_loudly_when_it_stays_down(settings):
    flaky = books_site()
    flaky.routes["/catalogue/page-2.html"] = [httpx.Response(503), fixture("books_page50.html")]
    assert len(crawl(settings, flaky).items) == 40
    down = books_site()
    down.routes["/catalogue/page-2.html"] = [httpx.Response(503)]
    with pytest.raises(FetchError):
        crawl(settings, down)
