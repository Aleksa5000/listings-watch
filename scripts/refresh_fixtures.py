"""Re-download the saved pages in tests/fixtures (a handful of polite requests).

    python scripts/refresh_fixtures.py

The tests never call this; it exists so you can see where the fixtures came from and refresh
them when a demo site changes. Pages are fetched with the same polite fetchers the scraper uses.
"""

import asyncio
from pathlib import Path

from listings_watch.config import Settings
from listings_watch.fetch import BrowserFetcher, Fetcher

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
RAW = {  # raw HTML exactly as the server sends it
    "books_page1.html": "https://books.toscrape.com/catalogue/page-1.html",
    "books_page50.html": "https://books.toscrape.com/catalogue/page-50.html",
    "quotes_page1.html": "https://quotes.toscrape.com/",
    # a tiny copy of the JS site, served locally by the browser test
    "site/js/index.html": "https://quotes.toscrape.com/js/",
    "site/js/page/2/index.html": "https://quotes.toscrape.com/js/page/2/",
    "site/static/jquery.js": "https://quotes.toscrape.com/static/jquery.js",
}


async def main() -> None:
    settings = Settings()
    async with Fetcher(settings) as fetch:
        for name, url in RAW.items():
            target = OUT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(await fetch(url), encoding="utf-8", newline="")
            print("saved", name)
    # the DOM after the page's JavaScript has run: what the browser source actually parses
    async with BrowserFetcher(settings, "div.quote") as fetch:
        html = await fetch("https://quotes.toscrape.com/js/")
        (OUT / "quotes_js_rendered_page1.html").write_text(html, encoding="utf-8", newline="")
        print("saved quotes_js_rendered_page1.html")


asyncio.run(main())
