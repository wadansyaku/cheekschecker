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
    send_monitor_slack_diagnostic,
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
        raise CalendarFetchError("connect timeout https://example.com/path?token=secret-value")

    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
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
        lambda payload, settings, **kwargs: events["notify"].append(payload),
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
    summary_text = json.dumps(events["summary"], ensure_ascii=False)
    assert "connect timeout" not in summary_text
    assert "secret-value" not in summary_text
    assert "連続失敗: 1回" in summary_text
    assert "前回成功取得: 未記録" in summary_text
    assert "前回通知後の抑制: 0回" in summary_text
    assert len(events["notify"]) == 1
    payload_text = json.dumps(events["notify"][0], ensure_ascii=False)
    assert "外部サイト取得失敗" in payload_text
    assert "connect timeout" not in payload_text
    assert "secret-value" not in payload_text
    assert "CalendarFetchError" not in payload_text
    assert "Traceback" not in payload_text
    assert "連続失敗: 1回" in payload_text
    assert "前回成功取得: 未記録" in payload_text
    assert "https://github.com/owner/repo/actions/runs/12345" in payload_text
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
        lambda payload, settings, **kwargs: events["notify"].append(payload),
    )

    monitor(make_settings(allow_fetch_failure=True, warning_throttle_minutes=180))

    assert events["notify"] == []
    assert "Slack warning suppressed" in events["summary"][0][2]
    throttle_state = events["saved"][0]["warning_throttle"]["monitor_fetch_failure"]
    assert throttle_state["consecutive_runs"] == 2
    assert throttle_state["suppressed_runs"] == 1


def test_monitor_fetch_failure_payload_reports_state_after_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = {"notify": [], "summary": [], "saved": []}
    last_warned_at = (datetime.now(tz=watch_cheeks.JST) - timedelta(minutes=181)).isoformat()
    last_fetched_at = "2024-01-09T10:00:00+09:00"

    async def failing_fetch_calendar_html(settings):
        raise CalendarFetchError("connect timeout with raw detail")

    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "98765")
    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", failing_fetch_calendar_html)
    monkeypatch.setattr(
        "watch_cheeks.load_state",
        lambda logical_today: {
            "last_fetched_at": last_fetched_at,
            "warning_throttle": {
                "monitor_fetch_failure": {
                    "last_seen_at": last_warned_at,
                    "last_warned_at": last_warned_at,
                    "consecutive_runs": 2,
                    "suppressed_runs": 3,
                    "last_category": "fetch_unavailable",
                }
            },
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
        lambda payload, settings, **kwargs: events["notify"].append(payload),
    )

    monitor(make_settings(allow_fetch_failure=True, warning_throttle_minutes=180))

    assert len(events["notify"]) == 1
    payload_text = json.dumps(events["notify"][0], ensure_ascii=False)
    assert "連続失敗: 3回" in payload_text
    assert "前回成功取得: 2024-01-09 10:00 JST" in payload_text
    assert "前回通知後の抑制: 3回" in payload_text
    assert "https://github.com/owner/repo/actions/runs/98765" in payload_text
    assert "connect timeout with raw detail" not in payload_text
    throttle_state = events["saved"][0]["warning_throttle"]["monitor_fetch_failure"]
    assert throttle_state["consecutive_runs"] == 3
    assert throttle_state["suppressed_runs"] == 0


def test_fetch_failure_payload_omits_invalid_action_url() -> None:
    payload, fallback, sections = watch_cheeks._build_fetch_failure_payload(
        title="Cheekschecker Monitor",
        message="外部サイト取得失敗",
        detail="connect timeout",
        target_url="not-a-url",
        category="fetch_unavailable",
        consecutive_runs=2,
        suppressed_runs=1,
        last_fetched_at="2024-01-09T10:00:00+09:00",
        detail_url="not-a-url",
    )

    assert "外部サイト取得失敗" in fallback
    payload_text = json.dumps(payload, ensure_ascii=False)
    sections_text = json.dumps(sections, ensure_ascii=False)
    assert "connect timeout" not in payload_text
    assert "connect timeout" not in fallback
    assert "connect timeout" not in sections_text
    assert "連続失敗: 2回" in payload_text
    assert "前回成功取得: 2024-01-09 10:00 JST" in payload_text
    assert "前回通知後の抑制: 1回" in sections_text
    assert sections[0][0] == "実行結果"
    assert all(block["type"] != "actions" for block in payload["blocks"])


def test_process_notifications_recovers_from_malformed_days_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = {"notify": [], "summary": [], "saved": []}
    logical_today = datetime.fromisoformat("2024-01-10T12:00:00+09:00").date()
    entry = watch_cheeks.DailyEntry(
        raw_date=logical_today,
        business_day=logical_today,
        day_of_month=logical_today.day,
        dow_en="Wed",
        male=3,
        female=6,
        single_female=4,
        total=9,
        ratio=0.667,
        considered=True,
        meets=True,
        required_single=3,
    )

    monkeypatch.setattr("watch_cheeks.save_state", lambda state: events["saved"].append(state))
    monkeypatch.setattr(
        "watch_cheeks.append_step_summary",
        lambda title, sections, fallback: events["summary"].append((title, sections, fallback)),
    )
    monkeypatch.setattr(
        "watch_cheeks.notify_slack",
        lambda payload, settings, **kwargs: events["notify"].append(payload),
    )

    result = watch_cheeks.process_notifications(
        [entry],
        settings=make_settings(),
        logical_today=logical_today,
        state={"days": ["stale", "shape"]},
    )

    saved_entry = result["days"][logical_today.isoformat()]
    assert saved_entry["stage"] == "initial"
    assert saved_entry["met"] is True
    assert len(events["notify"]) == 1


def test_process_notifications_step_summary_uses_public_safe_bands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = {"notify": [], "summary": [], "saved": []}
    logical_today = datetime.fromisoformat("2024-01-10T12:00:00+09:00").date()
    entry = watch_cheeks.DailyEntry(
        raw_date=logical_today,
        business_day=logical_today,
        day_of_month=logical_today.day,
        dow_en="Wed",
        male=3,
        female=6,
        single_female=4,
        total=9,
        ratio=0.667,
        considered=True,
        meets=True,
        required_single=3,
    )

    monkeypatch.setattr("watch_cheeks.save_state", lambda state: events["saved"].append(state))
    monkeypatch.setattr(
        "watch_cheeks.append_step_summary",
        lambda title, sections, fallback: events["summary"].append((title, sections, fallback)),
    )
    monkeypatch.setattr(
        "watch_cheeks.notify_slack",
        lambda payload, settings, **kwargs: events["notify"].append(payload),
    )

    watch_cheeks.process_notifications(
        [entry],
        settings=make_settings(),
        logical_today=logical_today,
        state={"days": {}},
    )

    summary_text = json.dumps(events["summary"], ensure_ascii=False)
    assert "単女3-4" in summary_text
    assert "女5-6" in summary_text
    assert "全<10" in summary_text
    assert "単女4 女6" not in summary_text
    assert "/全9" not in summary_text
    assert "単女4 女6" in events["notify"][0]["text"]


def test_notify_slack_can_use_public_safe_log_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    settings = make_settings(slack_webhook_url=None)
    payload = {"text": "単女4 女6 /全9", "blocks": []}

    monkeypatch.setattr(
        "watch_cheeks._send_slack_message",
        lambda webhook, payload, fallback_text, **kwargs: calls.append(
            (webhook, payload, fallback_text, kwargs)
        ),
    )

    watch_cheeks.notify_slack(
        payload,
        settings,
        fallback_text="単女3-4 女5-6 全<10",
    )

    assert calls[0][1]["text"] == "単女4 女6 /全9"
    assert calls[0][2] == "単女3-4 女5-6 全<10"


def test_short_error_message_redacts_urls_and_token_values() -> None:
    message = watch_cheeks._short_error_message(
        CalendarFetchError("failed https://example.com/path?token=secret-value&x=1")
    )

    assert "https://example.com" not in message
    assert "secret-value" not in message
    assert "[redacted url]" in message


def test_process_notifications_recovers_from_unknown_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = {"notify": [], "summary": [], "saved": []}
    logical_today = datetime.fromisoformat("2024-01-10T12:00:00+09:00").date()
    entry = watch_cheeks.DailyEntry(
        raw_date=logical_today,
        business_day=logical_today,
        day_of_month=logical_today.day,
        dow_en="Wed",
        male=3,
        female=6,
        single_female=4,
        total=9,
        ratio=0.667,
        considered=True,
        meets=True,
        required_single=3,
    )

    monkeypatch.setattr("watch_cheeks.save_state", lambda state: events["saved"].append(state))
    monkeypatch.setattr(
        "watch_cheeks.append_step_summary",
        lambda title, sections, fallback: events["summary"].append((title, sections, fallback)),
    )
    monkeypatch.setattr(
        "watch_cheeks.notify_slack",
        lambda payload, settings, **kwargs: events["notify"].append(payload),
    )

    result = watch_cheeks.process_notifications(
        [entry],
        settings=make_settings(),
        logical_today=logical_today,
        state={
            "days": {
                logical_today.isoformat(): {
                    "met": True,
                    "stage": "surprise",
                    "last_notified_at": "not-a-timestamp",
                }
            }
        },
    )

    saved_entry = result["days"][logical_today.isoformat()]
    assert saved_entry["stage"] == "initial"
    assert "初回" in events["notify"][0]["text"]


def test_monitor_slack_diagnostic_sends_synthetic_payload_without_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = {"notify": [], "summary": [], "save": 0}

    monkeypatch.setattr(
        "watch_cheeks.notify_slack",
        lambda payload, settings, strict=False, **kwargs: events["notify"].append((payload, strict)),
    )
    monkeypatch.setattr(
        "watch_cheeks.append_step_summary",
        lambda title, sections, fallback: events["summary"].append((title, sections, fallback)),
    )
    monkeypatch.setattr("watch_cheeks.save_state", lambda state: events.__setitem__("save", events["save"] + 1))

    send_monitor_slack_diagnostic(
        make_settings(ping_channel=True),
        logical_today=datetime.fromisoformat("2024-01-10T12:00:00+09:00").date(),
    )

    assert events["save"] == 0
    assert len(events["notify"]) == 1
    payload, strict = events["notify"][0]
    assert strict is True
    assert "【診断】" in payload["text"]
    assert "初回" in payload["text"]
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "synthetic payload" in rendered
    assert "<!channel>" not in rendered
    assert "@channel" not in rendered
    assert "@here" not in rendered
    assert len(events["summary"]) == 1
    assert events["summary"][0][0] == watch_cheeks.STEP_SUMMARY_TITLE_MONITOR


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
