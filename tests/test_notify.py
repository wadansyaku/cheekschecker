import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from watch_cheeks import (  # noqa: E402
    DEFAULT_ROLLOVER_HOURS,
    DailyEntry,
    Settings,
    evaluate_stage_transition,
    process_notifications,
)


def make_settings(**overrides):
    base = Settings(
        target_url="http://example.com",
        slack_webhook_url=None,
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


def make_entry(single, female, *, business_day=date(2024, 1, 10)):
    male = max(female - single, 0)
    total = male + female
    ratio = round(female / total, 3) if total else 0.0
    return DailyEntry(
        raw_date=business_day,
        business_day=business_day,
        day_of_month=business_day.day,
        dow_en="Wed",
        male=male,
        female=female,
        single_female=single,
        total=total,
        ratio=ratio,
        considered=True,
        meets=True,
        required_single=3,
    )


def test_evaluate_stage_transition_flow():
    now_ts = 1000
    entry = make_entry(3, 4)

    action, stage, last = evaluate_stage_transition(
        entry,
        None,
        now_ts=now_ts,
        cooldown_seconds=60,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
    )
    assert action == "initial"
    assert stage == "initial"
    assert last == now_ts

    # No bonus yet
    action, stage, last = evaluate_stage_transition(
        entry,
        {"stage": "initial", "last_notified_at": now_ts, "met": True},
        now_ts=now_ts + 10,
        cooldown_seconds=60,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
    )
    assert action == "bonus"
    assert stage == "bonus"
    assert last == now_ts + 10

    # Additional improvement within cooldown does not re-trigger
    entry_bonus = make_entry(5, 6)
    action, stage, last = evaluate_stage_transition(
        entry_bonus,
        {"stage": "bonus", "last_notified_at": now_ts + 10, "met": True},
        now_ts=now_ts + 20,
        cooldown_seconds=60,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
    )
    assert action is None
    assert stage == "bonus"
    assert last == now_ts + 10
    
    # Cooldown retains bonus stage
    action, stage, last = evaluate_stage_transition(
        entry_bonus,
        {"stage": "bonus", "last_notified_at": now_ts + 20, "met": True},
        now_ts=now_ts + 40,
        cooldown_seconds=60,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
    )
    assert action is None
    assert stage == "bonus"

    # Cooldown elapsed resets stage to initial without action
    action, stage, last = evaluate_stage_transition(
        entry_bonus,
        {"stage": "bonus", "last_notified_at": now_ts + 20, "met": True},
        now_ts=now_ts + 100,
        cooldown_seconds=60,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
    )
    assert action is None
    assert stage == "initial"


def test_process_notifications_filters_past(monkeypatch, tmp_path):
    captured = []

    def fake_notify(payload, settings):
        captured.append(payload)

    monkeypatch.setattr("watch_cheeks.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)
    monkeypatch.setattr("watch_cheeks.time", type("T", (), {"time": staticmethod(lambda: 2000)}))

    settings = make_settings()
    logical_today = date(2024, 1, 10)

    past_entry = make_entry(3, 4, business_day=date(2024, 1, 9))
    today_entry = make_entry(3, 4, business_day=logical_today)

    process_notifications([past_entry, today_entry], settings=settings, logical_today=logical_today)

    assert len(captured) == 1
    assert "初回" in captured[0]["text"]
    assert logical_today.strftime("%d") in captured[0]["text"]
