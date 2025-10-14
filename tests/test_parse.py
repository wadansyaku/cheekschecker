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


def test_parse_day_entries_counts_and_single_logic():
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    stats = parse_day_entries(html, BASE_SETTINGS)

    assert [entry["day"] for entry in stats] == [1, 2, 3, 4, 5, 6, 7]

    day1 = next(entry for entry in stats if entry["day"] == 1)
    assert day1["male"] == 1
    assert day1["female"] == 2  # スタッフ行もカウントされる
    assert day1["single_female"] == 1
    assert "スタッフ♀A" in day1["entries"]
    assert pytest.approx(day1["ratio"], 0.01) == 2 / 3
    assert day1["dow"] == "Sun"

    day2 = next(entry for entry in stats if entry["day"] == 2)
    assert day2["female"] == 1  # ♀A → female=1, single=1
    assert day2["single_female"] == 1
    assert "T-TIME" not in day2["entries"]

    day3 = next(entry for entry in stats if entry["day"] == 3)
    assert day3["female"] == 2  # ♀♀B → female=2, single=0
    assert day3["single_female"] == 0

    day4 = next(entry for entry in stats if entry["day"] == 4)
    assert day4["female"] == 5  # ♀2人C→2, ♀×3なお→3
    assert day4["single_female"] == 0

    day5 = next(entry for entry in stats if entry["day"] == 5)
    assert day5["male"] == 2  # ♂♂♀♀toshi
    assert day5["female"] == 2
    assert "POLE" not in day5["entries"]

    day6 = next(entry for entry in stats if entry["day"] == 6)
    assert day6["single_female"] == 5
    assert day6["female"] == 5
    assert day6["male"] == 1
    assert day6["dow"] == "Fri"

    day7 = next(entry for entry in stats if entry["day"] == 7)
    assert day7["single_female"] == 5
    assert day7["female"] == 5
    assert day7["male"] == 0
    assert day7["dow"] == "Sat"


def test_evaluate_conditions_with_weekday_thresholds():
    stats = [
        {"day": 10, "dow": "Fri", "dow_en": "Fri", "single_female": 5, "female": 6, "male": 4, "total": 10, "ratio": 0.6},
        {"day": 11, "dow": "Fri", "dow_en": "Fri", "single_female": 4, "female": 5, "male": 2, "total": 7, "ratio": 0.71},
        {"day": 12, "dow": "Tue", "dow_en": "Tue", "single_female": 3, "female": 3, "male": 2, "total": 5, "ratio": 0.6},
        {"day": 13, "dow": "Tue", "dow_en": "Tue", "single_female": 3, "female": 3, "male": 5, "total": 8, "ratio": 0.375},
        {"day": 14, "dow": "Sun", "dow_en": "Sun", "single_female": 4, "female": 4, "male": 0, "total": 4, "ratio": 1.0},
    ]
    cfg = replace(
        BASE_SETTINGS,
        female_min=3,
        female_ratio_min=0.5,
        min_total=4,
        include_dow=("Fri", "Tue"),
    )

    evaluated = evaluate_conditions(stats, cfg)

    entry1, entry2, entry3, entry4, entry5 = evaluated
    assert entry1["considered"] is True
    assert entry1["meets"] is True  # Fri: 単女5&女性比0.6>=0.5
    assert entry1["required_single_female"] == 5

    assert entry2["considered"] is True
    assert entry2["meets"] is False  # 単女不足

    assert entry3["considered"] is True
    assert entry3["meets"] is True  # Tue: 単女3&女性比0.6>=0.5
    assert entry3["required_single_female"] == 3

    assert entry4["considered"] is True
    assert entry4["meets"] is False  # 女性比不足

    assert entry5["considered"] is False  # INCLUDE_DOW 対象外
    assert entry5["meets"] is False


def test_diff_changes_newly_and_changed_detection():
    prev = {
        "1": {"male": 1, "female": 2, "single_female": 1, "total": 3, "ratio": 0.667, "meets": False},
    }
    stats = [
        {
            "day": 1,
            "male": 1,
            "female": 3,
            "single_female": 2,
            "total": 4,
            "ratio": 0.75,
            "meets": True,
            "considered": True,
        },
        {
            "day": 2,
            "male": 0,
            "female": 3,
            "single_female": 3,
            "total": 3,
            "ratio": 1.0,
            "meets": True,
            "considered": True,
        },
        {
            "day": 3,
            "male": 0,
            "female": 2,
            "single_female": 2,
            "total": 2,
            "ratio": 1.0,
            "meets": True,
            "considered": False,
        },
    ]

    changed, newly_met, status_changes = diff_changes(prev, stats)

    assert {entry["day"] for entry in changed} == {1, 2}
    assert {entry["day"] for entry in newly_met} == {1, 2}
    assert {entry["day"] for entry in status_changes} == {1, 2}
