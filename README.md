# listings-watch

[![CI](https://github.com/Aleksa5000/listings-watch/actions/workflows/ci.yml/badge.svg)](https://github.com/Aleksa5000/listings-watch/actions/workflows/ci.yml)

**Listings from several sites (including JavaScript sites) in one clean database: no duplicates, removed listings flagged, an alert when a scraper breaks, and an API / CSV / Excel export.**

A production-style scraping pipeline in Python: pluggable sources, a real headless browser for JavaScript pages, change tracking between runs, health checks that stop a broken scraper from corrupting your data, and a scheduled run that needs no manual work.

## The problem it solves

Most scraping jobs are not "download this page once". They are "keep this data current, from several sites, and tell me when it stops working". That means:

- the same item shows up on more than one site, or twice on the same site (**duplicates**);
- some sites only show their content after JavaScript runs (**a plain HTTP request sees an empty page**);
- listings disappear, and a naive scraper either keeps them forever or deletes everything the day a site is down (**removed listings**);
- sites change their layout, the scraper quietly returns nothing, and nobody notices for a week (**silent breakage**);
- someone has to remember to run it (**scheduling**) and the results have to reach a spreadsheet or another system (**API / CSV / Excel**).

listings-watch handles each of these explicitly. The demo targets are scraping practice sites ([books.toscrape.com](https://books.toscrape.com) and [quotes.toscrape.com](https://quotes.toscrape.com)); the structure is the same one you would use for real estate, job boards, classifieds, or product catalogues.

## Features

- **Source plugins with one interface.** A source is a small class: a start URL and a `parse_page()` function. Pagination, politeness and retries are written once. Demo sources: a static site (httpx + BeautifulSoup) and a JavaScript-rendered site (Playwright, async Chromium).
- **One normalized `Listing` model** (Pydantic): `source`, `external_id`, `title`, `price`, `currency`, `url`, `extra` (free-form JSON), `first_seen`, `last_seen`, `status` (`active` / `removed`).
- **Deduplication.** Stable key `(source, external_id)` inside a source, plus a configurable fuzzy check (rapidfuzz) that links the same item across different sources. Duplicates are kept, flagged with `duplicate_of` and `duplicate_score`, and hidden from exports by default.
- **Removed-listing detection.** A listing missing for N consecutive complete runs (default 2) is marked `removed`. If it comes back it is reactivated. Runs that stopped early (`--max-pages`) never remove anything.
- **Run bookkeeping.** Every run stores new / updated / unchanged / removed counts per source, pages read, duration and any error.
- **Health checks.** A source that returns 0 items, or more than 50% fewer than its recent average, is treated as broken: nothing is stored or removed for it, the failure is recorded, a Slack/Discord-compatible webhook is called, and the process exits with code 1. Other sources still run.
- **Polite by design.** Respects `robots.txt`, sends a descriptive User-Agent, waits between requests, retries with exponential backoff (and `Retry-After`). No proxies, no anti-bot evasion.
- **Output.** CLI export to CSV or Excel, and a small FastAPI service (`/listings`, `/listings.csv`, `/listings.xlsx`, `/runs`, `/health`). Text from scraped pages is neutralised so it cannot become a spreadsheet formula.
- **Scheduled.** A GitHub Actions workflow runs it daily and uploads the CSV and XLSX as artifacts; cron and Windows Task Scheduler recipes are included.
- **Database-agnostic.** SQLite out of the box; any SQLAlchemy URL (PostgreSQL) works.

## Architecture

```mermaid
flowchart LR
    SCHED["Scheduler<br/>GitHub Actions / cron / Task Scheduler"] --> CLI["listings-watch run"]

    subgraph SRC["Source plugins"]
        direction TB
        A["books<br/>static HTML"]
        B["quotes<br/>static HTML"]
        C["quotes_js<br/>JavaScript page"]
    end

    CLI --> SRC
    A --> HTTP["Fetcher: httpx<br/>robots.txt, delay, retries"]
    B --> HTTP
    C --> BROWSER["BrowserFetcher: Playwright<br/>headless Chromium, waits for render"]
    HTTP --> PARSE["parse_page()<br/>HTML to Listing"]
    BROWSER --> PARSE

    PARSE --> HEALTH{"Health check<br/>0 items or big drop?"}
    HEALTH -- "broken or failed" --> ALERT["Webhook alert<br/>exit code 1<br/>data left untouched"]
    HEALTH -- "healthy" --> STORE["Upsert + fuzzy dedupe<br/>+ removed detection"]
    STORE --> DB[("SQLite / PostgreSQL<br/>listings, runs, source_runs")]

    DB --> EXPORT["CSV / Excel export"]
    DB --> API["FastAPI<br/>/listings /runs /health"]
```

Code layout:

| Module | Job |
|---|---|
| `listings_watch/sources/` | Source plugins (`books.py`, `quotes.py`) and the shared crawl loop (`base.py`) |
| `fetch.py` | `Fetcher` (httpx) and `BrowserFetcher` (Playwright): robots.txt, pacing, retries |
| `models.py` | The normalized `Listing` |
| `store.py` | Upsert, new/updated/unchanged counting, removed detection, queries |
| `dedupe.py` | Cross-source fuzzy duplicate matching |
| `health.py` | "Is this result trustworthy?" decision |
| `runner.py` | One run: scrape each source, health-check, store, record, alert |
| `alerts.py` | Webhook call |
| `export.py`, `api.py`, `cli.py` | CSV/Excel, HTTP API, command line |

## Sample output

Real output from this project on 2026-10-13 (Windows 11, Python 3.14), three runs against the live demo sites. **The demo sites never change, so between run 1 and run 2 I edited the database to simulate upstream changes:** I raised two stored book prices (so the next scrape sees them as changed) and added three "Withdrawn Title" books that exist in the database but not on the site. Everything else below is unedited output.

```text
$ python -m listings_watch run
Run #1
  books      OK     found 1000  new 1000  updated   0  unchanged    0  removed   0  dupes   0  pages 50   54.0s
  quotes     OK     found  100  new  100  updated   0  unchanged    0  removed   0  dupes   0  pages 10   12.5s
  quotes_js  OK     found  100  new  100  updated   0  unchanged    0  removed   0  dupes 100  pages 10   24.1s
All sources healthy.

$ python -m listings_watch run          # run 2, after the simulated upstream changes
Run #2
  books      OK     found 1000  new    0  updated   2  unchanged  998  removed   0  dupes   0  pages 50   52.4s
  quotes     OK     found  100  new    0  updated   0  unchanged  100  removed   0  dupes   0  pages 10   11.9s
  quotes_js  OK     found  100  new    0  updated   0  unchanged  100  removed   0  dupes   0  pages 10   16.2s
All sources healthy.

$ python -m listings_watch run          # run 3: the 3 withdrawn books were missing twice in a row
Run #3
  books      OK     found 1000  new    0  updated   0  unchanged 1000  removed   3  dupes   0  pages 50   53.1s
  quotes     OK     found  100  new    0  updated   0  unchanged  100  removed   0  dupes   0  pages 10   11.4s
  quotes_js  OK     found  100  new    0  updated   0  unchanged  100  removed   0  dupes   0  pages 10   16.0s
All sources healthy.

$ python -m listings_watch report
Latest run #3 at 2026-10-13 04:26 UTC
  books      OK     found 1000  new 0  updated 0  unchanged 1000  removed 3
  quotes     OK     found 100  new 0  updated 0  unchanged 100  removed 0
  quotes_js  OK     found 100  new 0  updated 0  unchanged 100  removed 0
Database:
  books      active   1000
  books      removed  3
  quotes     active   100
  quotes_js  active   100
  cross-source duplicates flagged: 100
```

The 100 `quotes_js` rows are flagged as duplicates because the JavaScript page shows the same quotes as the static page. That is the cross-source check at work: both sites are scraped, but the export lists each quote once.

### When a scraper breaks

A second simulation: a local copy of the books page whose layout changed (HTTP 200, but the product cards are gone), with the alert sent to a local webhook receiver that prints what Slack or Discord would get. The real parser, health check, database and CLI are used.

```text
$ ALERT_WEBHOOK_URL=http://127.0.0.1:<port>/... python -m listings_watch run --source books
ERROR books looks broken: returned 0 items (site down, blocked, or layout changed)
--- webhook received POST ---
listings-watch: 1 of 1 source(s) need attention (run #4)
- books: BROKEN - returned 0 items (site down, blocked, or layout changed)
Nothing was marked removed for these sources; their last good data is untouched.
--- keys: ['content', 'text']
Run #4
  books      BROKEN returned 0 items (site down, blocked, or layout changed)
1 source(s) need attention; alert sent.
$ echo $?
1
```

The 1000 active books stayed active, and `GET /health` reports `"status": "degraded"` until the next healthy run.

### CSV export

`python -m listings_watch export` (first rows of `exports/listings.csv`, 1103 rows; the sheet has the same columns in Excel):

```csv
source,external_id,title,price,currency,url,status,first_seen,last_seen,duplicate_of,duplicate_score,extra
books,a-light-in-the-attic_1000,A Light in the Attic,51.77,GBP,https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html,active,2026-10-13T04:24:17,2026-10-13T04:27:32,,,"{""availability"": ""In stock"", ""rating"": 3}"
books,tipping-the-velvet_999,Tipping the Velvet,53.74,GBP,https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html,active,2026-10-13T04:24:17,2026-10-13T04:27:32,,,"{""availability"": ""In stock"", ""rating"": 1}"
books,soumission_998,Soumission,50.1,GBP,https://books.toscrape.com/catalogue/soumission_998/index.html,active,2026-10-13T04:24:17,2026-10-13T04:27:32,,,"{""availability"": ""In stock"", ""rating"": 1}"
```

With `--include-duplicates` a duplicate row shows where it points and how similar the titles were (`1001` is the id of the static-page copy, `100.0` the similarity score):

```csv
quotes_js,c8f5f748e3300068,The world as we have created it is a process of our thinking. It cannot be changed without changing our thinking.,,,https://quotes.toscrape.com/js/,active,2026-10-13T04:24:53,2026-10-13T04:27:59,1001,100.0,"{""author"": ""Albert Einstein"", ""tags"": [""change"", ""deep-thoughts"", ""thinking"", ""world""]}"
```

`--status removed` gave the 3 withdrawn books. Dates are UTC.

### API

`python -m listings_watch serve --port 18765`, then (numbers are what the server returned for the database above):

| Request | Result |
|---|---|
| `GET /listings?limit=1` | `total: 1103` (duplicates hidden) |
| `GET /listings?include_duplicates=true&limit=1` | `total: 1203` |
| `GET /listings?status=removed` | the 3 withdrawn books |
| `GET /listings?source=books&q=light&limit=2` | `total: 14`; first two: "A Light in the Attic", "A Flight of Arrows (The Pathfinders #2)" |
| `GET /listings.csv?source=quotes` | 200, `text/csv`, 100 rows plus header |
| `GET /listings.xlsx` | 200, an Excel workbook with 1103 rows plus header |
| `GET /runs?limit=2` | newest first, each run with its per-source counts |
| `GET /health` | `status`, listing totals, and the last run (`ok`, `degraded` or `no_runs`) |
| `GET /listings?status=bogus` | 422 |

Interactive docs are at `/docs`.

## Quickstart

```bash
git clone https://github.com/Aleksa5000/listings-watch.git
cd listings-watch
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m playwright install chromium    # only needed for JavaScript sources

python -m listings_watch run             # scrape all sources (about 1.5 minutes with polite delays)
python -m listings_watch report          # what changed in the last run
python -m listings_watch export --format xlsx
python -m listings_watch serve           # API on http://127.0.0.1:8080  (docs at /docs)
```

`python -m pip install -e .` also gives you a `listings-watch` command. On Linux CI machines use `python -m playwright install --with-deps chromium` to pull in the browser's system libraries.

| Command | What it does |
|---|---|
| `run [--source NAME]... [--max-pages N] [--delay S]` | Scrape, store, health-check, alert. Exit code `1` if any source is broken or failed |
| `report` | Latest run per source and totals in the database |
| `export [--format csv\|xlsx] [--out FILE] [--status active\|removed] [--source NAME] [--include-duplicates]` | Write listings to a file (default `exports/listings.<fmt>`) |
| `serve [--host 127.0.0.1] [--port 8080]` | Read-only HTTP API |

Global options go before the command: `--db FILE_OR_URL`, `-v` (log every page).

## Configuration

Everything is optional and comes from environment variables (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `ALERT_WEBHOOK_URL` | unset | Slack or Discord incoming-webhook URL. Unset = alerts skipped silently. The payload carries both `text` (Slack) and `content` (Discord) |
| `LW_DB_URL` | `sqlite:///data/listings.db` | SQLAlchemy URL (a bare file path also works with `--db`) |
| `LW_USER_AGENT` | `listings-watch/0.1 (+https://github.com/Aleksa5000/listings-watch; polite demo scraper)` | Identify yourself honestly; change it to include your contact when scraping for real |
| `LW_DELAY` | `1.0` | Seconds between requests to a site |
| `LW_ATTEMPTS` | `3` | Tries per request; backoff doubles each time (1s, 2s, ...) |
| `LW_TIMEOUT` | `20` | Seconds per request or page load |
| `LW_MAX_PAGES` | `0` | `0` = follow pagination to the end. A capped run is "incomplete": it never marks listings removed and is not compared with full runs |
| `LW_REMOVED_AFTER` | `2` | Consecutive complete runs without a listing before it is marked `removed` |
| `LW_FUZZY_THRESHOLD` | `92` | Cross-source near-duplicate similarity, 0-100. Lower = more matches, more false positives |
| `LW_DROP_THRESHOLD` | `0.5` | A source returning more than this fraction fewer items than its recent average is "broken" |
| `LW_HEALTH_WINDOW` | `5` | How many recent good, complete runs make up that average |

## Adding a new source

A source is one file. The crawl loop, politeness, retries, health check and storage come for free.

```python
# listings_watch/sources/myshop.py
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from listings_watch.models import Listing
from listings_watch.sources.base import HttpSource, ParsedPage  # BrowserSource for JavaScript sites


class MyShopSource(HttpSource):
    name = "myshop"  # used in --source, the database and exports
    start_url = "https://shop.example.com/items?page=1"

    def parse_page(self, html: str, url: str) -> ParsedPage:
        soup = BeautifulSoup(html, "html.parser")
        items, skipped = [], 0
        for card in soup.select("div.item"):
            link, price = card.select_one("a.title"), card.select_one(".price")
            if link is None or price is None:
                skipped += 1  # counted and logged, not silently lost
                continue
            items.append(
                Listing(
                    source=self.name,
                    external_id=card["data-sku"],  # must be stable between runs
                    title=link.get_text(strip=True),
                    price=float(price.get_text(strip=True).lstrip("$")),
                    currency="USD",
                    url=urljoin(url, link["href"]),
                    extra={"brand": card.get("data-brand")},
                )
            )
        nxt = soup.select_one("a.next")
        return ParsedPage(items, urljoin(url, nxt["href"]) if nxt else None, skipped)
```

1. Subclass `HttpSource` (plain HTML) or `BrowserSource` (JavaScript: also set `ready_selector`, an element that only exists once the page has rendered, as `quotes_js` does).
2. Register it in `SOURCES` in `listings_watch/sources/__init__.py`.
3. Save a page into `tests/fixtures/` and add a parser test next to the ones in `tests/test_parsers.py`. `parse_page` is a pure function, so the test needs no network.
4. Try it: `python -m listings_watch run --source myshop --max-pages 1`.

Pick an `external_id` that does not change (a SKU, a slug, an ID in the URL). If a site exposes a JSON API, use it instead of parsing HTML: override `scrape()` and return a `ScrapeResult`.

## Scheduling

**GitHub Actions** (`.github/workflows/scheduled.yml`) runs every day at 05:17 UTC and can be started by hand (Actions tab, "Run workflow", optionally choosing sources). It installs the dependencies and Chromium, scrapes, exports `listings.csv` and `listings.xlsx`, and uploads them as a workflow artifact (kept for 30 days). It never commits data back. If a source is broken the job fails, so GitHub emails you, and the webhook fires if you set the repository secret `ALERT_WEBHOOK_URL`.

The SQLite file is carried from one run to the next in the Actions cache, so "new / unchanged / removed" keeps working. A cache is best-effort storage (entries are evicted after 7 days without use); for a real deployment run it on a small VPS or point `LW_DB_URL` at PostgreSQL.

**cron** (Linux/macOS), daily at 05:15, with a log:

```cron
15 5 * * *  cd /opt/listings-watch && ALERT_WEBHOOK_URL="https://hooks.slack.com/services/..." .venv/bin/python -m listings_watch run >> logs/run.log 2>&1
```

**Windows Task Scheduler:**

```bat
schtasks /Create /SC DAILY /ST 05:15 /TN "listings-watch" /TR "cmd /c cd /d C:\listings-watch && .venv\Scripts\python.exe -m listings_watch run >> logs\run.log 2>&1"
```

Set `ALERT_WEBHOOK_URL` as a user environment variable. A broken source makes the task end with a non-zero "Last Run Result".

## Using PostgreSQL

```bash
python -m pip install "psycopg[binary]"
export LW_DB_URL="postgresql+psycopg://user:password@localhost:5432/listings"
python -m listings_watch run
```

Tables are created on first use. The code uses only portable SQLAlchemy constructs (no SQLite-specific SQL), but see "What was tested" below: only SQLite has been run.

## What was tested

- **91 offline tests** (`python -m pytest`, no network): parsers on saved pages from the demo sites (including the browser-rendered DOM of the JavaScript page and a layout-change case); dedupe and fuzzy matching with different thresholds; removed-detection over simulated runs (consecutive misses, reappearance, capped runs, per-source isolation, duplicate promotion); health-check and alert logic with a mocked webhook (0 items, drop of 51% vs exactly 50%, exceptions, broken runs not poisoning the average, webhook failure); fetcher retries, backoff, `Retry-After`, robots.txt and delay pacing against a mocked transport; the API with FastAPI's `TestClient`; CSV and Excel export including formula neutralisation; the CLI including exit codes.
- **A real headless-Chromium test, offline:** it serves two saved pages of the JavaScript demo site plus the site's jQuery from a local server and checks that the browser source renders them, follows pagination and fails loudly when nothing renders.
- **2 optional live smoke tests** (`python -m pytest -m live`) against the real demo sites. Skipped by default; both passed on 2026-10-13.
- **Manual end-to-end:** the three real runs shown above, the simulated breakage with a local webhook receiver, every API endpoint hit with `curl`, and a fresh `pip install` of the package in a clean virtual environment with its `listings-watch` command started.
- I also broke the removal rule on purpose twice to confirm the tests fail when it is wrong.

**Not verified:**

- The two GitHub Actions workflows have not run on GitHub yet (YAML syntax checked locally; the shell snippet in the scrape step tested separately). In particular the cache-based hand-over of the database between scheduled runs is untested.
- Everything ran on Windows 11 with Python 3.14.5. CI targets Linux and Python 3.12; that combination is untested until the first CI run.
- PostgreSQL has not been run.
- Alerts were delivered to a local receiver and to mocks, not to a real Slack or Discord channel.
- The XLSX file was re-read with openpyxl, not opened in Excel itself. The cron and Task Scheduler commands were not executed.

## Legal and ethics

Scrape only what you are allowed to scrape. Read a site's terms of service and `robots.txt`, prefer an official API when one exists, and do not collect personal data you have no basis to hold. This project checks `robots.txt` before every fetch, identifies itself with a User-Agent, waits between requests and backs off on errors. It has no proxy rotation, CAPTCHA solving or fingerprint spoofing, and it will not get you past a site that does not want to be scraped. The demo uses practice sites that exist to be scraped. If you adapt it to a real site, set `LW_USER_AGENT` to include a way to contact you, and keep `LW_DELAY` generous.

## Limitations

- **No history of changes.** A listing stores its latest values and an `updated` counter, not every previous price. (My [price-tracker-scraper](https://github.com/Aleksa5000/price-tracker-scraper) project is built around price history.)
- **Fuzzy matching compares titles only**, and only new or reactivated listings are checked, so changing `LW_FUZZY_THRESHOLD` does not re-evaluate old rows. Short, generic titles can produce false positives; tune the threshold per use case.
- **Sources run one after another** and a run is one process. Two runs at the same time against one SQLite file are not supported.
- **The API has no authentication.** It binds to `127.0.0.1` by default; put it behind a reverse proxy with auth before exposing it.
- **No migrations.** Tables are created automatically; a schema change on an existing database would need Alembic.
- **Sites with logins, CAPTCHAs or active bot protection are out of scope** on purpose.
- **The demo "listings" are books and quotes.** The model is generic (`price`, `currency` and `extra` are optional), but the demo has no real estate or job data.

## Tech stack

Python 3.11+, httpx, BeautifulSoup, Playwright (Chromium), SQLAlchemy 2, Pydantic 2, rapidfuzz, Typer, FastAPI + Uvicorn, openpyxl, pytest, ruff, GitHub Actions.

## License

All rights reserved. This is a portfolio sample: you can read and run the code to evaluate my work, but you may not use it commercially, redistribute it or build on it without my written permission. See [LICENSE](LICENSE).

---

**Need something similar? Contact me on Upwork:** Aleksa Djorovic, Python automation, web scraping and AI.
