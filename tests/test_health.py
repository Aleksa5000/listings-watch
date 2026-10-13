"""Health check, alerting, and what a broken source does (and does not do) to the database."""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from helpers import FakeSource, items_for
from listings_watch.alerts import send_alert
from listings_watch.db import ListingRow, SourceRunRow
from listings_watch.health import check_health
from listings_watch.runner import run_pipeline


class Webhook:
    """Stands in for the Slack/Discord webhook call."""

    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, url, text):
        self.calls.append((url, text))
        return self.ok


def run(settings, sessions, sources, hook):
    return asyncio.run(run_pipeline(settings, sessions, sources, notify=hook))


# --- the pure decision --------------------------------------------------------------------


def test_zero_items_is_always_broken():
    assert not check_health(0, []).ok
    assert not check_health(0, [100, 100], complete=False).ok


def test_drop_of_more_than_half_is_broken_exactly_half_is_not():
    history = [100, 100, 100]
    assert check_health(51, history).ok
    assert check_health(50, history).ok  # a 50% drop is not "more than 50%"
    bad = check_health(49, history)
    assert not bad.ok and "51% below" in bad.reason


def test_no_history_means_only_the_zero_check_applies():
    assert check_health(3, []).ok


def test_capped_runs_are_not_compared_with_full_runs():
    assert check_health(20, [1000, 1000], complete=False).ok


def test_threshold_is_configurable():
    assert not check_health(70, [100], drop_threshold=0.2).ok
    assert check_health(70, [100], drop_threshold=0.5).ok


# --- the pipeline -------------------------------------------------------------------------


def test_healthy_runs_send_no_alert_and_exit_zero(settings, sessions):
    hook = Webhook()
    src = FakeSource("shop", items_for("shop", 5))
    s1 = run(settings, sessions, [src], hook)
    s2 = run(settings, sessions, [src], hook)
    assert (s1.exit_code, s2.exit_code) == (0, 0)
    assert hook.calls == []
    assert (s1.outcomes[0].counts.new, s2.outcomes[0].counts.unchanged) == (5, 5)


def test_empty_source_is_broken_nothing_removed_alert_sent(settings, sessions):
    hook = Webhook()
    src = FakeSource("shop", items_for("shop", 5))
    run(settings, sessions, [src], hook)
    src.items = []  # site returns an empty page, e.g. a block page or a layout change
    for _ in range(3):  # more runs than removed_after: still nothing may be removed
        summary = run(settings, sessions, [src], hook)
        assert summary.exit_code == 1
        assert summary.outcomes[0].status == "broken"
    with sessions() as s:
        rows = s.scalars(select(ListingRow)).all()
        assert [r.status for r in rows] == ["active"] * 5
        assert [r.missed_runs for r in rows] == [0] * 5
        failures = s.scalars(select(SourceRunRow).where(SourceRunRow.status == "broken")).all()
        assert len(failures) == 3 and "0 items" in failures[0].error
    assert len(hook.calls) == 3  # one alert per failing run
    url, text = hook.calls[0]
    assert url == settings.alert_webhook_url
    assert "shop: BROKEN" in text and "0 items" in text and "run #2" in text


def test_big_drop_is_broken_and_keeps_old_data(settings, sessions):
    hook = Webhook()
    src = FakeSource("shop", items_for("shop", 100))
    run(settings, sessions, [src], hook)
    src.items = items_for("shop", 40)  # lost 60% of the items
    summary = run(settings, sessions, [src], hook)
    assert summary.outcomes[0].status == "broken" and "60% below" in summary.outcomes[0].error
    with sessions() as s:
        assert len(s.scalars(select(ListingRow).where(ListingRow.status == "active")).all()) == 100
    assert len(hook.calls) == 1


def test_moderate_drop_is_a_normal_update_and_removals_follow_the_rules(settings, sessions):
    src = FakeSource("shop", items_for("shop", 100))
    run(settings, sessions, [src], Webhook())
    src.items = items_for("shop", 70)  # 30% fewer: plausible, not a failure
    first = run(settings, sessions, [src], Webhook())
    second = run(settings, sessions, [src], Webhook())
    assert first.exit_code == second.exit_code == 0
    assert (first.outcomes[0].counts.removed, second.outcomes[0].counts.removed) == (0, 30)


def test_broken_runs_do_not_poison_the_average(settings, sessions):
    src = FakeSource("shop", items_for("shop", 100))
    run(settings, sessions, [src], Webhook())
    src.items = items_for("shop", 10)
    for _ in range(3):  # three broken runs in a row ...
        assert run(settings, sessions, [src], Webhook()).exit_code == 1
    src.items = items_for("shop", 95)  # ... recovery is judged against the good runs only
    assert run(settings, sessions, [src], Webhook()).exit_code == 0


def test_exception_marks_source_failed_and_other_sources_still_run(settings, sessions):
    hook = Webhook()
    good, bad = FakeSource("good", items_for("good", 3)), FakeSource("bad", items_for("bad", 3))
    run(settings, sessions, [good, bad], hook)
    bad.error = RuntimeError("Timeout 20000ms exceeded")
    summary = run(settings, sessions, [bad, good], hook)
    by_name = {o.source: o for o in summary.outcomes}
    assert by_name["bad"].status == "failed" and "RuntimeError" in by_name["bad"].error
    assert by_name["good"].status == "ok" and by_name["good"].counts.unchanged == 3
    assert summary.exit_code == 1 and len(hook.calls) == 1
    assert "1 of 2 source(s)" in hook.calls[0][1]


def test_no_webhook_configured_skips_alert_but_still_fails(settings, sessions):
    from dataclasses import replace

    hook = Webhook()
    src = FakeSource("shop", [])
    summary = run(replace(settings, alert_webhook_url=None), sessions, [src], hook)
    assert summary.exit_code == 1 and hook.calls == [] and not summary.alert_sent


def test_failed_webhook_delivery_does_not_crash_the_run(settings, sessions):
    summary = run(settings, sessions, [FakeSource("shop", [])], Webhook(ok=False))
    assert summary.exit_code == 1 and not summary.alert_sent


def test_capped_run_is_stored_but_does_not_remove(settings, sessions):
    src = FakeSource("shop", items_for("shop", 100))
    run(settings, sessions, [src], Webhook())
    src.items, src.complete = items_for("shop", 20), False  # e.g. --max-pages 1
    for _ in range(3):
        summary = run(settings, sessions, [src], Webhook())
        assert summary.exit_code == 0 and summary.outcomes[0].counts.removed == 0
    with sessions() as s:
        assert len(s.scalars(select(ListingRow).where(ListingRow.status == "removed")).all()) == 0


# --- the webhook call itself --------------------------------------------------------------


def test_send_alert_posts_slack_and_discord_compatible_json():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(204)

    ok = send_alert(
        "https://hooks.example.test/x", "scraper broke", transport=httpx.MockTransport(handler)
    )
    assert ok
    assert seen == [
        ("https://hooks.example.test/x", {"text": "scraper broke", "content": "scraper broke"})
    ]


def test_send_alert_without_url_does_nothing():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("no request expected")

    assert send_alert(None, "x", transport=httpx.MockTransport(handler)) is False
    assert send_alert("", "x", transport=httpx.MockTransport(handler)) is False


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down")),
    ],
)
def test_send_alert_swallows_delivery_errors(handler):
    assert (
        send_alert("https://hooks.example.test/x", "x", transport=httpx.MockTransport(handler))
        is False
    )


def test_send_alert_truncates_for_discord():
    sizes = []

    def handler(request):
        sizes.append(len(json.loads(request.content)["content"]))
        return httpx.Response(200)

    send_alert("https://hooks.example.test/x", "a" * 5000, transport=httpx.MockTransport(handler))
    assert sizes == [1900]
