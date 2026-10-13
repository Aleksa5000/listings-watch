"""books.toscrape.com: a static catalogue made for scraping practice (50 pages, 1000 books)."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from listings_watch.models import Listing
from listings_watch.sources.base import HttpSource, ParsedPage

CURRENCIES = {"£": "GBP", "$": "USD", "€": "EUR"}
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def parse_price(text: str) -> tuple[float, str | None] | None:
    """'£51.77' -> (51.77, 'GBP'). Also survives the 'Â£51.77' mojibake some servers produce."""
    number = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not number:
        return None
    currency = next((code for sign, code in CURRENCIES.items() if sign in text), None)
    return float(number.group().replace(",", "")), currency


class BooksSource(HttpSource):
    name = "books"
    start_url = "https://books.toscrape.com/catalogue/page-1.html"

    def parse_page(self, html: str, url: str) -> ParsedPage:
        soup = BeautifulSoup(html, "html.parser")
        items: list[Listing] = []
        skipped = 0
        for card in soup.select("article.product_pod"):
            link = card.select_one("h3 a")
            price_tag = card.select_one("p.price_color")
            price = parse_price(price_tag.get_text()) if price_tag else None
            if link is None or not link.get("title") or not link.get("href") or price is None:
                skipped += 1
                continue
            href = urljoin(url, link["href"])
            slug = href.rstrip("/").split("/")
            external_id = slug[-2] if slug[-1] == "index.html" else slug[-1]
            stars = card.select_one("p.star-rating")
            rating = next(
                (RATINGS[c] for c in (stars.get("class") if stars else []) if c in RATINGS), None
            )
            stock = card.select_one("p.availability")
            items.append(
                Listing(
                    source=self.name,
                    external_id=external_id,
                    title=link["title"],
                    price=price[0],
                    currency=price[1],
                    url=href,
                    extra={
                        "rating": rating,
                        "availability": stock.get_text(strip=True) if stock else None,
                    },
                )
            )
        nxt = soup.select_one("li.next a")
        return ParsedPage(
            items, urljoin(url, nxt["href"]) if nxt and nxt.get("href") else None, skipped
        )
