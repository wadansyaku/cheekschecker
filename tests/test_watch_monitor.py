import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from watch_cheeks import (  # noqa: E402
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
        debug_summary=False,
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
