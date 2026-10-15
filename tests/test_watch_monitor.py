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
        "watch_cheeks.parse_day_entries", lambda html, settings, reference_date: [watch_cheeks._build_monitor_diagnostic_entry(reference_date)]
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
    monkeypatch.setattr("watch_cheeks.parse_day_entries", lambda html, settings, reference_date: [watch_cheeks._build_monitor_diagnostic_entry(reference_date)])
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
    assert "単独女性 4人 · 女性 6人" in events["notify"][0]["text"]
    assert "単独女性 4人" not in summary_text


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


@pytest.mark.parametrize(
    "headers,previous,expected",
    [
        ({"ETag": "new", "Last-Modified": "same"}, {"etag": "old", "last_modified": "same"}, False),
        ({"ETag": "same", "Last-Modified": "new"}, {"etag": "same", "last_modified": "old"}, True),
        ({"Last-Modified": "same"}, {"last_modified": "same"}, True),
    ],
)
def test_head_validator_precedence(monkeypatch, headers, previous, expected):
    class Response:
        def raise_for_status(self):
            pass

    response = Response()
    response.headers = headers
    monkeypatch.setattr(watch_cheeks.requests, "head", lambda *args, **kwargs: response)
    previous["last_fetched_at"] = "2024-01-16T12:00:00+09:00"
    skip, _ = watch_cheeks.should_skip_by_http_headers(
        make_settings(), previous, now=datetime.fromisoformat("2024-01-16T12:10:00+09:00")
    )
    assert skip is expected


@pytest.mark.parametrize("reason", ["business_day", "fetch_failure"])
def test_same_headers_do_not_skip_business_day_change_or_recovery(monkeypatch, reason):
    class Response:
        def __init__(self):
            self.headers = {"ETag": "same"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(watch_cheeks.requests, "head", lambda *args, **kwargs: Response())
    state = {"etag": "same", "last_fetched_at": "2024-01-16T04:59:00+09:00"}
    if reason == "fetch_failure":
        state["last_fetched_at"] = "2024-01-16T05:00:00+09:00"
        state["warning_throttle"] = {"monitor_fetch_failure": {"consecutive_runs": 1}}
    skip, _ = watch_cheeks.should_skip_by_http_headers(
        make_settings(), state, now=datetime.fromisoformat("2024-01-16T05:01:00+09:00")
    )
    assert skip is False


@pytest.mark.parametrize("empty_parse", [False, True])
@pytest.mark.parametrize("allow_failure", [False, True])
def test_failed_source_does_not_commit_new_validators_or_freshness(monkeypatch, empty_parse, allow_failure):
    original = {"days": {}, "etag": "old", "last_fetched_at": "2024-01-15T12:00:00+09:00"}
    saved = []

    async def fetch(settings):
        if not empty_parse:
            raise CalendarFetchError("unavailable")
        return "<html>maintenance</html>", "http://example.com"

    monkeypatch.setattr(watch_cheeks, "check_robots_allow", lambda settings: True)
    monkeypatch.setattr(watch_cheeks, "load_state", lambda day: dict(original))
    monkeypatch.setattr(watch_cheeks, "should_skip_by_http_headers", lambda settings, state: (False, {"etag": "new"}))
    monkeypatch.setattr(watch_cheeks, "fetch_calendar_html", fetch)
    monkeypatch.setattr(watch_cheeks, "parse_day_entries", lambda *args, **kwargs: [])
    monkeypatch.setattr(watch_cheeks, "save_state", lambda state: saved.append(state))
    monkeypatch.setattr(watch_cheeks, "notify_slack", lambda *args, **kwargs: None)
    monkeypatch.setattr(watch_cheeks, "update_masked_history", lambda *args, **kwargs: pytest.fail("must not update history"))
    if allow_failure:
        monitor(make_settings(allow_fetch_failure=True))
    else:
        with pytest.raises(CalendarFetchError):
            monitor(make_settings(allow_fetch_failure=False))
    assert saved[0]["etag"] == "old"
    assert saved[0]["last_fetched_at"] == original["last_fetched_at"]
    assert saved[0]["warning_throttle"]["monitor_fetch_failure"]["consecutive_runs"] == 1


def test_failed_warning_delivery_does_not_throttle_the_next_attempt(monkeypatch):
    saved = []

    async def fetch(settings):
        raise CalendarFetchError("unavailable")

    def fail_send(*args, **kwargs):
        raise RuntimeError("Slack delivery failed")

    monkeypatch.setattr(watch_cheeks, "check_robots_allow", lambda settings: True)
    monkeypatch.setattr(watch_cheeks, "load_state", lambda day: {"days": {}})
    monkeypatch.setattr(watch_cheeks, "should_skip_by_http_headers", lambda settings, state: (False, {}))
    monkeypatch.setattr(watch_cheeks, "fetch_calendar_html", fetch)
    monkeypatch.setattr(watch_cheeks, "save_state", lambda state: saved.append(state))
    monkeypatch.setattr(watch_cheeks, "notify_slack", fail_send)
    with pytest.raises(RuntimeError, match="Slack delivery failed"):
        monitor(make_settings(allow_fetch_failure=True))
    warning = saved[0]["warning_throttle"]["monitor_fetch_failure"]
    assert warning["last_warned_at"] is None
    assert warning["consecutive_runs"] == 1
    assert warning["suppressed_runs"] == 0


def _prepare_outage_recovery(monkeypatch, tmp_path, *, warning_delivered=True, meets=False):
    settings = make_settings()
    today = watch_cheeks.derive_business_day(datetime.now(tz=watch_cheeks.JST), settings.rollover_hours)
    entry = replace(watch_cheeks._build_monitor_diagnostic_entry(today),
                    meets=meets, single_female=3, female=3, male=7, total=10, ratio=0.3)
    monkeypatch.setattr(watch_cheeks, "MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
    watch_cheeks.save_state({
        "days": {}, "warning_throttle": {"monitor_fetch_failure": {
            "consecutive_runs": 2,
            "last_warned_at": "2026-10-01T12:00:00+09:00" if warning_delivered else None,
        }},
    })

    async def fetch(settings):
        return "synthetic source", "https://example.com"

    monkeypatch.setattr(watch_cheeks, "check_robots_allow", lambda settings: True)
    monkeypatch.setattr(watch_cheeks, "should_skip_by_http_headers", lambda settings, state: (False, {}))
    monkeypatch.setattr(watch_cheeks, "fetch_calendar_html", fetch)
    monkeypatch.setattr(watch_cheeks, "parse_day_entries", lambda *args, **kwargs: [entry])
    monkeypatch.setattr(watch_cheeks, "update_masked_history", lambda *args, **kwargs: None)
    return settings, today


@pytest.mark.parametrize("warning_delivered", [False, True])
def test_monitor_closes_only_delivered_outage_warning_once(monkeypatch, tmp_path, warning_delivered):
    settings, today = _prepare_outage_recovery(monkeypatch, tmp_path, warning_delivered=warning_delivered)
    sent = []
    monkeypatch.setattr(watch_cheeks, "notify_slack", lambda payload, settings, **kwargs: sent.append(payload))
    monitor(settings)
    monitor(settings)
    assert len(sent) == int(warning_delivered)
    if sent:
        assert "監視が復旧" in sent[0]["text"]
        assert "<!channel>" not in json.dumps(sent[0])
        assert "単女" not in sent[0]["text"]
    assert watch_cheeks.load_state(today)["warning_throttle"]["monitor_fetch_failure"]["consecutive_runs"] == 0


def test_ordinary_notification_failure_does_not_send_recovery_or_advance_freshness(monkeypatch, tmp_path):
    settings, today = _prepare_outage_recovery(monkeypatch, tmp_path, meets=True)
    sent = []

    def fail(payload, settings, **kwargs):
        sent.append(payload)
        raise RuntimeError("notification failed")

    monkeypatch.setattr(watch_cheeks, "notify_slack", fail)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="notification failed"):
            monitor(settings)
    assert len(sent) == 2
    assert all("監視が復旧" not in payload["text"] for payload in sent)
    state = watch_cheeks.load_state(today)
    assert state["warning_throttle"]["monitor_fetch_failure"]["consecutive_runs"] == 2
    assert not state.get("last_fetched_at")
    assert state["days"] == {}


def test_failed_recovery_retries_without_repeating_accepted_ordinary_notification(monkeypatch, tmp_path):
    settings, today = _prepare_outage_recovery(monkeypatch, tmp_path, meets=True)
    sent = []

    def send(payload, settings, **kwargs):
        sent.append(payload)
        if "監視が復旧" in payload["text"] and len(sent) == 2:
            raise RuntimeError("recovery failed")

    monkeypatch.setattr(watch_cheeks, "notify_slack", send)
    # Recovery-only failure must let the workflow's success-gated commit run.
    # It retains the pending warning without losing accepted ordinary markers.
    monitor(settings)
    state = watch_cheeks.load_state(today)
    assert state["warning_throttle"]["monitor_fetch_failure"]["consecutive_runs"] == 2
    assert state["days"][today.isoformat()]["stage"] == "initial"
    assert state["last_fetched_at"]
    monitor(settings)
    monitor(settings)
    assert len(sent) == 3
    assert "監視が復旧" not in sent[0]["text"]
    assert all("監視が復旧" in payload["text"] for payload in sent[1:])
    assert watch_cheeks.load_state(today)["warning_throttle"]["monitor_fetch_failure"]["consecutive_runs"] == 0


def test_all_diagnostics_use_production_builders_without_fetch_or_state(monkeypatch):
    sent = []
    forbidden = lambda *args, **kwargs: pytest.fail("diagnostic must not access live data")
    for name in ("load_state", "save_state", "load_masked_history", "update_masked_history", "fetch_calendar_html"):
        monkeypatch.setattr(watch_cheeks, name, forbidden)
    monkeypatch.setattr(watch_cheeks, "notify_slack", lambda payload, settings, **kwargs: sent.append(payload))
    watch_cheeks.send_slack_diagnostic_suite(make_settings(ping_channel=True))
    assert len(sent) == 5
    rendered = json.dumps(sent, ensure_ascii=False)
    assert rendered.count("【診断】") >= 5
    assert "synthetic payload" in rendered
    assert "<!channel>" not in rendered
    assert "比較データ不足" in rendered
    assert "未取得" in rendered
    assert "観測なし" in rendered
    assert "監視が復旧" in rendered
    assert "新規成立日" not in json.dumps(sent[0], ensure_ascii=False)


def test_diagnostic_scenario_cli_is_bounded():
    parser = watch_cheeks.build_arg_parser()
    assert parser.parse_args(["monitor-diagnostic", "--scenario", "all"]).scenario == "all"
    with pytest.raises(SystemExit):
        parser.parse_args(["monitor-diagnostic", "--scenario", "live"])
