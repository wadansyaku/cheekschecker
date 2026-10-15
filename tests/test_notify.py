import sys
import json
from dataclasses import replace
from datetime import date, timedelta
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


def test_process_notifications_mentions_channel_for_stage_notifications(monkeypatch, tmp_path):
    captured = []

    def fake_notify(payload, settings, **kwargs):
        captured.append(payload)

    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)
    monkeypatch.setattr("watch_cheeks.time", type("T", (), {"time": staticmethod(lambda: 2000)}))

    process_notifications(
        [make_entry(3, 4)],
        settings=make_settings(ping_channel=True),
        logical_today=date(2024, 1, 10),
    )

    rendered = json.dumps(captured[0], ensure_ascii=False)
    assert rendered.count("<!channel>") == 1
    assert "基準達成通知" in rendered


def test_process_notifications_does_not_mention_channel_for_count_update_only(
    monkeypatch,
    tmp_path,
):
    captured = []
    logical_today = date(2024, 1, 10)

    def fake_notify(payload, settings, **kwargs):
        captured.append(payload)

    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)
    monkeypatch.setattr("watch_cheeks.time", type("T", (), {"time": staticmethod(lambda: 2010)}))

    process_notifications(
        [make_entry(4, 6, business_day=logical_today)],
        settings=make_settings(ping_channel=True, notify_mode="changed"),
        logical_today=logical_today,
        state={
            "days": {
                logical_today.isoformat(): {
                    "met": True,
                    "stage": "bonus",
                    "last_notified_at": 2000,
                    "counts": {
                        "female": 4,
                        "single_female": 3,
                        "total": 4,
                    },
                }
            }
        },
    )

    rendered = json.dumps(captured[0], ensure_ascii=False)
    assert "人数更新" in rendered
    assert "<!channel>" not in rendered
    assert "@channel" not in rendered


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

    def fake_notify(payload, settings, **kwargs):
        captured.append(payload)

    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
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
    saved = (tmp_path / "monitor_state.json").read_text(encoding="utf-8")
    assert "\"counts\"" not in saved
    assert "\"single_female\"" not in saved


def test_process_notifications_limits_to_today_and_tomorrow(monkeypatch, tmp_path):
    captured = []

    def fake_notify(payload, settings, **kwargs):
        captured.append(payload)

    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)
    monkeypatch.setattr("watch_cheeks.time", type("T", (), {"time": staticmethod(lambda: 2000)}))

    settings = make_settings()
    logical_today = date(2024, 1, 10)

    today_entry = make_entry(3, 4, business_day=logical_today)
    tomorrow_entry = make_entry(3, 4, business_day=logical_today + timedelta(days=1))
    future_entry = make_entry(3, 4, business_day=logical_today + timedelta(days=2))

    process_notifications(
        [today_entry, tomorrow_entry, future_entry],
        settings=settings,
        logical_today=logical_today,
    )

    assert len(captured) == 1
    text = captured[0]["text"]
    assert "明日" in text
    assert "2日後" not in text


def test_process_notifications_keeps_first_day_month_boundary_state_separate(
    monkeypatch,
    tmp_path,
):
    captured = []

    def fake_notify(payload, settings, **kwargs):
        captured.append(payload)

    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)
    monkeypatch.setattr("watch_cheeks.time", type("T", (), {"time": staticmethod(lambda: 2000)}))

    logical_today = date(2026, 5, 31)
    existing_may_first = {
        "met": True,
        "stage": "initial",
        "last_notified_at": 1000,
    }

    process_notifications(
        [make_entry(3, 4, business_day=date(2026, 6, 1))],
        settings=make_settings(),
        logical_today=logical_today,
        state={"days": {"2026-05-01": existing_may_first}},
    )

    assert len(captured) == 1
    assert "初回" in captured[0]["text"]
    assert "明日: 6/1(月)" in captured[0]["text"]
    assert "5/1" not in captured[0]["text"]

    saved = json.loads((tmp_path / "monitor_state.json").read_text(encoding="utf-8"))
    assert saved["days"]["2026-05-01"] == existing_may_first
    assert saved["days"]["2026-06-01"]["met"] is True


def test_process_notifications_filters_stale_previous_month_first_day(
    monkeypatch,
    tmp_path,
):
    captured = []

    def fake_notify(payload, settings, **kwargs):
        captured.append(payload)

    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
    monkeypatch.setattr("watch_cheeks.notify_slack", fake_notify)
    monkeypatch.setattr("watch_cheeks.time", type("T", (), {"time": staticmethod(lambda: 2000)}))

    process_notifications(
        [make_entry(3, 4, business_day=date(2026, 5, 1))],
        settings=make_settings(),
        logical_today=date(2026, 6, 1),
        state={"days": {}},
    )

    assert captured == []


def test_failed_delivery_keeps_state_and_retries_next_run(monkeypatch, tmp_path):
    import requests

    path = tmp_path / "monitor_state.json"
    original = {"days": {}}
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", path)
    calls = []

    def fail_post(*args, **kwargs):
        calls.append(kwargs["json"])
        raise requests.Timeout("timeout")

    monkeypatch.setattr("src.notifications.requests.post", fail_post)
    settings = make_settings(slack_webhook_url="https://hooks.slack.test/services/example")
    entry = make_entry(3, 4)
    with pytest.raises(RuntimeError, match="notification failed"):
        process_notifications([entry], settings=settings, logical_today=entry.business_day, state=original)
    assert original == {"days": {}}
    assert json.loads(path.read_text()) == original
    assert len(calls) == 1  # No ambiguous fallback re-send for monitor.

    class Accepted:
        def raise_for_status(self):
            pass

    def accept_post(*args, **kwargs):
        calls.append(kwargs["json"])
        assert json.loads(path.read_text()) == original  # Persist only after acceptance.
        return Accepted()

    monkeypatch.setattr("src.notifications.requests.post", accept_post)
    process_notifications([entry], settings=settings, logical_today=entry.business_day, state=original)
    assert len(calls) == 2
    saved = json.loads(path.read_text())
    assert saved["days"][entry.business_day.isoformat()]["stage"] == "initial"
    assert saved["days"][entry.business_day.isoformat()]["last_notified_at"] is not None


def test_missing_webhook_does_not_mark_pending_notification_as_delivered(monkeypatch, tmp_path):
    path = tmp_path / "monitor_state.json"
    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", path)
    entry = make_entry(3, 4)
    with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL is not set"):
        process_notifications([entry], settings=make_settings(), logical_today=entry.business_day, state={"days": {}})
    assert not path.exists()


def test_initial_notification_has_one_day_entry_and_accessible_context(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr("watch_cheeks.MONITOR_STATE_PATH", tmp_path / "monitor_state.json")
    monkeypatch.setattr("watch_cheeks.notify_slack", lambda payload, settings, **kwargs: captured.append(payload))
    entry = make_entry(3, 4)
    process_notifications(
        [entry], settings=make_settings(ping_channel=True), logical_today=entry.business_day,
        state={"days": {}, "last_fetched_at": "2024-01-10T12:00:00+09:00"},
    )
    payload = captured[0]
    assert payload["text"].count("今日: 1/10(水)") == 1
    assert "新規で条件を満たした日" not in payload["text"]
    assert "確認: 2024-01-10 12:00 JST" in payload["text"]
    assert payload["blocks"][0]["type"] == "header"
    assert "営業日: 2024-01-10" in json.dumps(payload["blocks"], ensure_ascii=False)
    assert payload["unfurl_links"] is False
