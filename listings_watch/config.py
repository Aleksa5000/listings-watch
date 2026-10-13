"""Settings come from environment variables, so the same code runs locally, in cron and in CI."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_USER_AGENT = (
    "listings-watch/0.1 (+https://github.com/Aleksa5000/listings-watch; polite demo scraper)"
)


@dataclass(frozen=True)
class Settings:
    db_url: str = "sqlite:///data/listings.db"
    alert_webhook_url: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    delay: float = 1.0  # seconds between requests to a site
    attempts: int = 3  # tries per request (exponential backoff between them)
    backoff: float = 1.0  # first backoff in seconds, doubles every retry
    timeout: float = 20.0
    removed_after: int = 2  # consecutive complete runs without a listing before it is "removed"
    fuzzy_threshold: float = 92.0  # 0-100, cross-source near-duplicate similarity
    drop_threshold: float = 0.5  # a source returning this fraction fewer items is "broken"
    health_window: int = 5  # recent good runs that make up a source's average
    max_pages: int = 0  # 0 = follow pagination to the end

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def read(name: str, default, cast):
            raw = env.get(name, "").strip()
            if not raw:
                return default
            try:
                return cast(raw)
            except ValueError:
                raise ValueError(f"{name}={raw!r} is not a valid {cast.__name__}") from None

        d = cls()
        return cls(
            db_url=read("LW_DB_URL", d.db_url, str),
            alert_webhook_url=env.get("ALERT_WEBHOOK_URL", "").strip() or None,
            user_agent=read("LW_USER_AGENT", d.user_agent, str),
            delay=read("LW_DELAY", d.delay, float),
            attempts=read("LW_ATTEMPTS", d.attempts, int),
            timeout=read("LW_TIMEOUT", d.timeout, float),
            removed_after=read("LW_REMOVED_AFTER", d.removed_after, int),
            fuzzy_threshold=read("LW_FUZZY_THRESHOLD", d.fuzzy_threshold, float),
            drop_threshold=read("LW_DROP_THRESHOLD", d.drop_threshold, float),
            health_window=read("LW_HEALTH_WINDOW", d.health_window, int),
            max_pages=read("LW_MAX_PAGES", d.max_pages, int),
        )
