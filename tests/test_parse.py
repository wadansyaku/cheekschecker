import sys
from dataclasses import replace
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

from watch_cheeks import (
    Settings,
    diff_changes,
    evaluate_conditions,
    parse_day_entries,
)

FIXTURE_PATH = Path("tests/fixtures/sample_yoyaku.html")

BASE_SETTINGS = Settings(
    target_url="http://example.com",
    slack_webhook_url=None,
    female_min=3,
    female_ratio_min=0.3,
    min_total=None,
    exclude_keywords=("スタッフ", "t-time", "pole"),
    include_dow=(),
    notify_mode="newly",
    debug_summary=False,
)


def test_parse_day_entries_counts_and_exclusions():
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    stats = parse_day_entries(html, BASE_SETTINGS)

    assert [entry["day"] for entry in stats] == [1, 2, 3, 4, 5, 6, 7]

    day1 = next(entry for entry in stats if entry["day"] == 1)
    assert day1["male"] == 1
    assert day1["female"] == 1  # スタッフ entry excluded
    assert pytest.approx(day1["ratio"], 0.01) == 0.5
    assert day1["dow_en"] == "Sun"

    day3 = next(entry for entry in stats if entry["day"] == 3)
    assert day3["male"] == 0
    assert day3["female"] == 2
    assert day3["dow_en"] == "Tue"

    day4 = next(entry for entry in stats if entry["day"] == 4)
    assert day4["male"] == 2
    assert day4["female"] == 2

    day6 = next(entry for entry in stats if entry["day"] == 6)
    assert day6["female"] == 2
    assert day6["male"] == 1
    assert day6["dow_en"] == "Fri"

    day7 = next(entry for entry in stats if entry["day"] == 7)
    assert day7["total"] == 0
    assert day7["dow_en"] == "Sat"


def test_evaluate_conditions_with_thresholds():
    stats = [
        {"day": 1, "dow_en": "Fri", "female": 3, "male": 2, "total": 5, "ratio": 0.6},
        {"day": 2, "dow_en": "Fri", "female": 3, "male": 0, "total": 3, "ratio": 1.0},
        {"day": 3, "dow_en": "Sun", "female": 4, "male": 0, "total": 4, "ratio": 1.0},
    ]
    cfg = replace(
        BASE_SETTINGS,
        female_min=3,
        female_ratio_min=0.5,
        min_total=4,
        include_dow=("Fri",),
    )
    evaluated = evaluate_conditions(stats, cfg)

    entry1, entry2, entry3 = evaluated
    assert entry1["considered"] is True
    assert entry1["meets"] is True

    assert entry2["considered"] is False  # MIN_TOTAL 未満
    assert entry2["meets"] is False

    assert entry3["considered"] is False  # INCLUDE_DOW 対象外
    assert entry3["meets"] is False


def test_diff_changes_newly_and_changed_detection():
    prev = {
        "1": {"male": 1, "female": 2, "total": 3, "meets": False},
    }
    stats = [
        {"day": 1, "male": 1, "female": 3, "total": 4, "ratio": 0.75, "meets": True, "considered": True},
        {"day": 2, "male": 0, "female": 3, "total": 3, "ratio": 1.0, "meets": True, "considered": True},
        {"day": 3, "male": 0, "female": 2, "total": 2, "ratio": 1.0, "meets": True, "considered": False},
    ]

    changed, newly_met, status_changes = diff_changes(prev, stats)

    assert {entry["day"] for entry in changed} == {1, 2}
    assert {entry["day"] for entry in newly_met} == {1, 2}
    assert {entry["day"] for entry in status_changes} == {1, 2}
