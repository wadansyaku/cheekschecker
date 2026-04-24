import sys
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import watch_cheeks

from watch_cheeks import (  # noqa: E402
    CalendarFetchError,
    DEFAULT_ROLLOVER_HOURS,
    Settings,
    monitor,
    sanitize_html,
)


def make_settings(**overrides) -> Settings:
    base = Settings(
        target_url="http://example.com",
        slack_webhook_url="http://hooks.slack.test",
        female_min=3,
        female_ratio_min=0.3,
        min_total=None,
        exclude_keywords=(),
        include_dow=(),
        notify_mode="newly",
        ping_channel=False,
        cooldown_minutes=1,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
        ignore_older_than=1,
        notify_from_today=1,
        rollover_hours=dict(DEFAULT_ROLLOVER_HOURS),
        mask_level=1,
        robots_enforce=False,
        ua_contact=None,
        allow_fetch_failure=False,
        head_skip_max_age_minutes=180,
    )
    return replace(base, **overrides) if overrides else base


def test_monitor_writes_nested_sanitized_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_fetch_calendar_html(settings):
        return "<html></html>", {}

    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", fake_fetch_calendar_html)
    monkeypatch.setattr("watch_cheeks.load_state", lambda logical_today: {})
    monkeypatch.setattr(
        "watch_cheeks.should_skip_by_http_headers", lambda settings, state: (False, {})
    )
    monkeypatch.setattr(
        "watch_cheeks.parse_day_entries", lambda html, settings, reference_date: []
    )
    monkeypatch.setattr("watch_cheeks.log_parsing_snapshot", lambda entries, logical_today: None)
    monkeypatch.setattr(
        "watch_cheeks.process_notifications",
        lambda entries, settings, logical_today, state: None,
    )
    monkeypatch.setattr("watch_cheeks.update_masked_history", lambda entries, settings: None)

    nested_path = tmp_path / "nested/sanitized.html"

    monitor(make_settings(), output_sanitized=nested_path)

    assert nested_path.exists()
    assert nested_path.read_text(encoding="utf-8").strip() == sanitize_html("<html></html>")


def test_monitor_persists_etag_and_skips_second_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monitor_state_path = tmp_path / "monitor_state.json"
    calls = {"fetch": 0}

    async def fake_fetch_calendar_html(settings):
        calls["fetch"] += 1
        return "<html></html>", {}

    class _HeadResponse:
        headers = {"ETag": "same-etag", "Last-Modified": "same-modified"}

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", monitor_state_path)
    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", fake_fetch_calendar_html)
    monkeypatch.setattr("watch_cheeks.parse_day_entries", lambda html, settings, reference_date: [])
    monkeypatch.setattr("watch_cheeks.log_parsing_snapshot", lambda entries, logical_today: None)
    monkeypatch.setattr(
        "watch_cheeks.process_notifications",
        lambda entries, settings, logical_today, state: watch_cheeks.save_state(state),
    )
    monkeypatch.setattr("watch_cheeks.update_masked_history", lambda entries, settings: None)
    monkeypatch.setattr("watch_cheeks.requests.head", lambda url, timeout=10: _HeadResponse())

    monitor(make_settings())
    monitor(make_settings())

    assert calls["fetch"] == 1
    saved = monitor_state_path.read_text(encoding="utf-8")
    assert "same-etag" in saved


def test_monitor_skips_when_fetch_fails_and_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    events = {"notify": [], "summary": [], "saved": []}

    async def failing_fetch_calendar_html(settings):
        raise CalendarFetchError("connect timeout")

    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", failing_fetch_calendar_html)
    monkeypatch.setattr("watch_cheeks.load_state", lambda logical_today: {})
    monkeypatch.setattr("watch_cheeks.save_state", lambda state: events["saved"].append(state))
    monkeypatch.setattr(
        "watch_cheeks.should_skip_by_http_headers", lambda settings, state: (False, {})
    )
    monkeypatch.setattr(
        "watch_cheeks.append_step_summary",
        lambda title, sections, fallback: events["summary"].append((title, sections, fallback)),
    )
    monkeypatch.setattr(
        "watch_cheeks.notify_slack",
        lambda payload, settings: events["notify"].append(payload),
    )
    monkeypatch.setattr(
        "watch_cheeks.parse_day_entries",
        lambda html, settings, reference_date: pytest.fail("parse_day_entries should not run"),
    )
    monkeypatch.setattr(
        "watch_cheeks.process_notifications",
        lambda entries, settings, logical_today, state: pytest.fail("process_notifications should not run"),
    )
    monkeypatch.setattr(
        "watch_cheeks.update_masked_history",
        lambda entries, settings: pytest.fail("update_masked_history should not run"),
    )

    monitor(make_settings(allow_fetch_failure=True))

    assert len(events["summary"]) == 1
    assert "外部サイト取得失敗" in events["summary"][0][2]
    assert len(events["notify"]) == 1
    assert "外部サイト取得失敗" in json.dumps(events["notify"][0], ensure_ascii=False)
    assert events["saved"][0]["warning_throttle"]["monitor_fetch_failure"]["consecutive_runs"] == 1


def test_monitor_fetch_failure_warning_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    events = {"notify": [], "summary": [], "saved": []}
    last_warned_at = (datetime.now(tz=watch_cheeks.JST) - timedelta(minutes=10)).isoformat()

    async def failing_fetch_calendar_html(settings):
        raise CalendarFetchError("connect timeout")

    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", failing_fetch_calendar_html)
    monkeypatch.setattr(
        "watch_cheeks.load_state",
        lambda logical_today: {
            "warning_throttle": {
                "monitor_fetch_failure": {
                    "last_seen_at": last_warned_at,
                    "last_warned_at": last_warned_at,
                    "consecutive_runs": 1,
                    "suppressed_runs": 0,
                    "last_category": "fetch_unavailable",
                }
            }
        },
    )
    monkeypatch.setattr("watch_cheeks.save_state", lambda state: events["saved"].append(state))
    monkeypatch.setattr(
        "watch_cheeks.should_skip_by_http_headers", lambda settings, state: (False, {})
    )
    monkeypatch.setattr(
        "watch_cheeks.append_step_summary",
        lambda title, sections, fallback: events["summary"].append((title, sections, fallback)),
    )
    monkeypatch.setattr(
        "watch_cheeks.notify_slack",
        lambda payload, settings: events["notify"].append(payload),
    )

    monitor(make_settings(allow_fetch_failure=True, warning_throttle_minutes=180))

    assert events["notify"] == []
    assert "Slack warning suppressed" in events["summary"][0][2]
    throttle_state = events["saved"][0]["warning_throttle"]["monitor_fetch_failure"]
    assert throttle_state["consecutive_runs"] == 2
    assert throttle_state["suppressed_runs"] == 1


def test_load_settings_redacts_webhook_in_debug_log(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "https://hooks.slack.test/secret-token"
    captured = []

    monkeypatch.setenv("SLACK_WEBHOOK_URL", secret)
    monkeypatch.setattr(watch_cheeks.LOGGER, "debug", lambda *args, **kwargs: captured.append((args, kwargs)))

    settings = watch_cheeks.load_settings()

    assert settings.slack_webhook_url == secret
    rendered = repr(captured)
    assert secret not in rendered
    assert "<set>" in rendered


def test_robots_parser_respects_allow_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RobotsResponse:
        status_code = 200
        text = """
User-agent: *
Disallow: /private/
Allow: /private/yoyaku.shtml
"""

    monkeypatch.setattr(watch_cheeks.requests, "get", lambda url, timeout=10: _RobotsResponse())

    settings = make_settings(
        target_url="http://example.com/private/yoyaku.shtml",
        robots_enforce=True,
    )

    assert watch_cheeks.check_robots_allow(settings) is True


def test_robots_parser_blocks_disallowed_target(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RobotsResponse:
        status_code = 200
        text = """
User-agent: *
Disallow: /private/
"""

    monkeypatch.setattr(watch_cheeks.requests, "get", lambda url, timeout=10: _RobotsResponse())

    settings = make_settings(
        target_url="http://example.com/private/yoyaku.shtml",
        robots_enforce=True,
    )

    assert watch_cheeks.check_robots_allow(settings) is False


def test_robots_parser_prefers_specific_agent_group(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RobotsResponse:
        status_code = 200
        text = """
User-agent: CheekscheckerBot
Disallow: /private/

User-agent: *
Allow: /
"""

    monkeypatch.setattr(watch_cheeks.requests, "get", lambda url, timeout=10: _RobotsResponse())

    settings = make_settings(
        target_url="http://example.com/private/yoyaku.shtml",
        robots_enforce=True,
    )

    assert watch_cheeks.check_robots_allow(settings) is False


def test_head_skip_requires_recent_successful_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    class _HeadResponse:
        headers = {"ETag": "same-etag", "Last-Modified": "same-modified"}

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(watch_cheeks.requests, "head", lambda url, timeout=10: _HeadResponse())
    settings = make_settings(head_skip_max_age_minutes=180)
    now = datetime.fromisoformat("2024-01-15T12:00:00+09:00")

    missing_fetch_skip, _ = watch_cheeks.should_skip_by_http_headers(
        settings,
        {"etag": "same-etag", "last_modified": "same-modified"},
        now=now,
    )
    recent_fetch_skip, _ = watch_cheeks.should_skip_by_http_headers(
        settings,
        {
            "etag": "same-etag",
            "last_modified": "same-modified",
            "last_fetched_at": (now - timedelta(minutes=30)).isoformat(),
        },
        now=now,
    )
    stale_fetch_skip, _ = watch_cheeks.should_skip_by_http_headers(
        settings,
        {
            "etag": "same-etag",
            "last_modified": "same-modified",
            "last_fetched_at": (now - timedelta(minutes=181)).isoformat(),
        },
        now=now,
    )

    assert missing_fetch_skip is False
    assert recent_fetch_skip is True
    assert stale_fetch_skip is False
