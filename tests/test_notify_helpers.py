"""Tests for process_notifications helper functions."""

import pytest
from datetime import date
from watch_cheeks import (
    DailyEntry,
    Settings,
    _build_notification_sections,
    _process_single_entry,
    _categorize_notifications,
)


def make_entry(meets=True, female=5, single_female=3, total=10):
    """Helper to create test entries."""
    return DailyEntry(
        raw_date=date(2025, 1, 15),
        business_day=date(2025, 1, 15),
        day_of_month=15,
        dow_en="Mon",
        male=total - female,
        female=female,
        single_female=single_female,
        total=total,
        ratio=female / total if total else 0.0,
        considered=True,
        meets=meets,
        required_single=3,
    )


def test_build_notification_sections_empty():
    sections = _build_notification_sections([], [], [], date(2025, 1, 15))
    assert sections == []


def test_build_notification_sections_with_newly_met():
    entry = make_entry()
    sections = _build_notification_sections([], [entry], [], date(2025, 1, 15))
    assert len(sections) == 1
    assert sections[0][0] == "新規成立日"
    assert len(sections[0][1]) == 1


def test_build_notification_sections_with_all_types():
    entry1 = make_entry()
    entry2 = make_entry(female=6)
    entry3 = make_entry(female=7)

    sections = _build_notification_sections(
        [(entry1, "initial")],
        [entry2],
        [entry3],
        date(2025, 1, 15)
    )

    assert len(sections) == 3
    assert sections[0][0] == "基準達成通知"
    assert sections[1][0] == "新規成立日"
    assert sections[2][0] == "人数更新"


def test_process_single_entry_creates_state():
    entry = make_entry()
    prev_state = {}
    settings = Settings(
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
        cooldown_minutes=180,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
        ignore_older_than=1,
        notify_from_today=1,
        rollover_hours={},
        mask_level=1,
        robots_enforce=False,
        ua_contact=None,
    )

    action, stage, last_notified, state_entry = _process_single_entry(
        entry, prev_state, 1000, settings
    )

    assert action == "initial"
    assert stage == "initial"
    assert last_notified == 1000
    assert "counts" in state_entry
    assert state_entry["counts"]["female"] == 5
    assert state_entry["stage"] == "initial"


def test_categorize_notifications_adds_to_newly_met():
    entry = make_entry(meets=True)
    prev_state = {"met": False}
    settings = Settings(
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
        cooldown_minutes=180,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
        ignore_older_than=1,
        notify_from_today=1,
        rollover_hours={},
        mask_level=1,
        robots_enforce=False,
        ua_contact=None,
    )

    stage_notifications = []
    newly_met = []
    changed_counts = []

    _categorize_notifications(
        entry, None, prev_state, settings,
        stage_notifications, newly_met, changed_counts
    )

    assert len(newly_met) == 1
    assert newly_met[0] == entry


def test_categorize_notifications_adds_to_changed_when_counts_differ():
    entry = make_entry(meets=True, female=6)
    prev_state = {"met": True, "counts": {"female": 5, "single_female": 3, "total": 10}}
    settings = Settings(
        target_url="http://example.com",
        slack_webhook_url=None,
        female_min=3,
        female_ratio_min=0.3,
        min_total=None,
        exclude_keywords=(),
        include_dow=(),
        notify_mode="changed",
        debug_summary=False,
        ping_channel=False,
        cooldown_minutes=180,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
        ignore_older_than=1,
        notify_from_today=1,
        rollover_hours={},
        mask_level=1,
        robots_enforce=False,
        ua_contact=None,
    )

    stage_notifications = []
    newly_met = []
    changed_counts = []

    _categorize_notifications(
        entry, None, prev_state, settings,
        stage_notifications, newly_met, changed_counts
    )

    assert len(changed_counts) == 1
    assert changed_counts[0] == entry


def test_categorize_notifications_handles_stage_action():
    entry = make_entry(meets=True)
    prev_state = {"met": True}
    settings = Settings(
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
        cooldown_minutes=180,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
        ignore_older_than=1,
        notify_from_today=1,
        rollover_hours={},
        mask_level=1,
        robots_enforce=False,
        ua_contact=None,
    )

    stage_notifications = []
    newly_met = []
    changed_counts = []

    _categorize_notifications(
        entry, "bonus", prev_state, settings,
        stage_notifications, newly_met, changed_counts
    )

    assert len(stage_notifications) == 1
    assert stage_notifications[0] == (entry, "bonus")
