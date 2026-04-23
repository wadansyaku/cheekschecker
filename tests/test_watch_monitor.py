import sys
import json
from dataclasses import replace
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
    events = {"notify": [], "summary": []}

    async def failing_fetch_calendar_html(settings):
        raise CalendarFetchError("connect timeout")

    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", failing_fetch_calendar_html)
    monkeypatch.setattr("watch_cheeks.load_state", lambda logical_today: {})
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
