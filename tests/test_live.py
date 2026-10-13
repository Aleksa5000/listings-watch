"""Smoke test against the real demo sites. Skipped by default (needs network and Chromium):

python -m pytest -m live
"""

import asyncio
from dataclasses import replace

import pytest

from listings_watch.sources import BooksSource, QuotesJsSource

pytestmark = pytest.mark.live


def test_live_books_first_page(settings):
    result = asyncio.run(BooksSource().scrape(replace(settings, delay=0.5), max_pages=1))
    assert len(result.items) == 20 and result.items[0].price > 0 and result.complete is False


def test_live_javascript_quotes_first_page(settings):
    result = asyncio.run(QuotesJsSource().scrape(replace(settings, delay=0.5), max_pages=1))
    assert len(result.items) == 10 and result.items[0].extra["author"]
