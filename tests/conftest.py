import pytest

from listings_watch.config import Settings
from listings_watch.db import make_engine, session_factory


@pytest.fixture
def settings(tmp_path):
    # no waiting in tests; webhook configured so alert logic is exercised
    return Settings(
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
        alert_webhook_url="https://hooks.example.test/T000/B000/secret",
        delay=0,
        backoff=0,
    )


@pytest.fixture
def engine(settings):
    return make_engine(settings.db_url)


@pytest.fixture
def sessions(engine):
    return session_factory(engine)
