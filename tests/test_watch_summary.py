import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from watch_cheeks import (  # noqa: E402
    DEFAULT_ROLLOVER_HOURS,
    DOW_EN,
    DailyEntry,
    Settings,
    SummaryBundle,
    summary,
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


def make_bundle() -> SummaryBundle:
    entry = DailyEntry(
        raw_date=date(2024, 1, 8),
        business_day=date(2024, 1, 8),
        day_of_month=8,
        dow_en="Mon",
        male=3,
        female=5,
        single_female=2,
        total=8,
        ratio=0.625,
        considered=True,
        meets=True,
        required_single=2,
    )
    return SummaryBundle(period_label="latest 7 days", period_days=[entry], previous_days=[entry])


def stub_summary_dependencies(monkeypatch: pytest.MonkeyPatch, bundle: SummaryBundle, payload: dict) -> None:
    async def fake_fetch_calendar_html(settings):
        return "<html />", {}

    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", fake_fetch_calendar_html)
    monkeypatch.setattr("watch_cheeks.parse_day_entries", lambda html, settings, reference_date: [])
    monkeypatch.setattr("watch_cheeks.update_masked_history", lambda entries, settings: None)
    monkeypatch.setattr(
        "watch_cheeks.select_summary_bundle",
        lambda entries, logical_today, days: bundle,
    )
    monkeypatch.setattr(
        "watch_cheeks.generate_summary_payload",
        lambda bundle, logical_today, settings: payload,
    )


def test_summary_notifies_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = make_bundle()
    payload = {"text": "ok"}
    stub_summary_dependencies(monkeypatch, bundle, payload)

    captured = []

    def fake_notify(body, settings) -> None:
        captured.append(body)

    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)

    summary(make_settings(), days=7)

    assert captured == [payload]


def test_summary_skips_notify_with_raw_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = make_bundle()
    payload = {"text": "ok"}
    stub_summary_dependencies(monkeypatch, bundle, payload)

    captured = []

    def fake_notify(body, settings) -> None:
        captured.append(body)

    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)

    raw_path = tmp_path / "raw.json"
    summary(make_settings(), days=7, raw_output=raw_path, notify=False)

    assert raw_path.exists()
    assert captured == []


def test_summary_skips_notify_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = make_bundle()
    payload = {"text": "ok"}
    stub_summary_dependencies(monkeypatch, bundle, payload)

    captured = []

    def fake_notify(body, settings) -> None:
        captured.append(body)

    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)

    summary(make_settings(), days=7, notify=False)

    assert captured == []


def test_summary_writes_nested_raw_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = make_bundle()
    payload = {"text": "ok"}
    stub_summary_dependencies(monkeypatch, bundle, payload)

    nested_path = tmp_path / "subdir/output.json"

    summary(make_settings(), days=7, raw_output=nested_path, notify=False)

    assert nested_path.exists()
    content = json.loads(nested_path.read_text(encoding="utf-8"))
    assert content["period_label"] == bundle.period_label


def test_summary_supplements_entries_from_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    logical_today = date(2024, 2, 10)
    dow_label = DOW_EN[(logical_today.weekday() + 1) % 7]

    parsed_entries = [
        DailyEntry(
            raw_date=logical_today,
            business_day=logical_today,
            day_of_month=logical_today.day,
            dow_en=dow_label,
            male=0,
            female=4,
            single_female=1,
            total=4,
            ratio=1.0,
            considered=True,
            meets=True,
            required_single=3,
        )
    ]

    cached_state = {
        "days": {
            "2024-01-15": {
                "counts": {"male": 1, "female": 3, "single_female": 1, "total": 4, "ratio": 0.75},
                "met": True,
            },
            # Should be filtered out because it's beyond the 60-day window
            "2023-12-20": {
                "counts": {"male": 0, "female": 2, "single_female": 1, "total": 2, "ratio": 1.0},
                "met": True,
            },
        }
    }

    monkeypatch.setattr("watch_cheeks.check_robots_allow", lambda settings: True)
    monkeypatch.setattr("watch_cheeks.derive_business_day", lambda now, rollover: logical_today)

    async def fake_fetch_calendar_html(settings):
        return "<html />", settings.target_url

    monkeypatch.setattr("watch_cheeks.fetch_calendar_html", fake_fetch_calendar_html)
    monkeypatch.setattr("watch_cheeks.parse_day_entries", lambda html, settings, reference_date: list(parsed_entries))
    monkeypatch.setattr("watch_cheeks.update_masked_history", lambda entries, settings: None)
    monkeypatch.setattr("watch_cheeks.load_state", lambda reference_date: cached_state)

    raw_path = tmp_path / "raw.json"
    summary(make_settings(), days=30, raw_output=raw_path, notify=False)

    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    dates = {item["date"] for item in payload["days"]}

    assert "2024-01-15" in dates
    assert "2023-12-20" not in dates
    # Ensure the originally parsed entry is kept
    assert logical_today.isoformat() in dates
