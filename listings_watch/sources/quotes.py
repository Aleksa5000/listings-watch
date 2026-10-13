"""quotes.toscrape.com, twice: the plain HTML pages and the JavaScript-rendered /js/ pages.

Both show the same quotes. The JS version only exists in the browser after its script has
run, so it is fetched with Playwright; the parser is shared because the rendered DOM matches.
That overlap also exercises the cross-source duplicate check.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from listings_watch.models import Listing
from listings_watch.sources.base import BrowserSource, HttpSource, ParsedPage, Source

QUOTE_MARKS = "“”\"' "


def parse_quotes(source: Source, html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    items: list[Listing] = []
    skipped = 0
    for card in soup.select("div.quote"):
        text, author = card.select_one(".text"), card.select_one(".author")
        if text is None or author is None or not text.get_text(strip=True):
            skipped += 1
            continue
        quote = text.get_text(strip=True).strip(QUOTE_MARKS)
        who = author.get_text(strip=True)
        # Quotes have no id on the page, so the id is a hash of what makes the quote itself.
        digest = hashlib.sha1(f"{who}\n{quote}".encode()).hexdigest()[:16]
        items.append(
            Listing(
                source=source.name,
                external_id=digest,
                title=quote,
                url=url,
                extra={
                    "author": who,
                    "tags": [t.get_text(strip=True) for t in card.select("a.tag")],
                },
            )
        )
    nxt = soup.select_one("li.next a")
    return ParsedPage(
        items, urljoin(url, nxt["href"]) if nxt and nxt.get("href") else None, skipped
    )


class QuotesSource(HttpSource):
    name = "quotes"
    start_url = "https://quotes.toscrape.com/"

    def parse_page(self, html: str, url: str) -> ParsedPage:
        return parse_quotes(self, html, url)


class QuotesJsSource(BrowserSource):
    name = "quotes_js"
    start_url = "https://quotes.toscrape.com/js/"
    ready_selector = "div.quote"

    def parse_page(self, html: str, url: str) -> ParsedPage:
        return parse_quotes(self, html, url)
