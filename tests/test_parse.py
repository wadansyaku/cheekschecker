import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from watch_cheeks import (  # noqa: E402
    DEFAULT_ROLLOVER_HOURS,
    JST,
    Settings,
    derive_business_day,
    infer_entry_date,
    parse_day_entries,
)

FIXTURE_PATH = Path("tests/fixtures/sample_yoyaku.html")
MODERN_FIXTURE_PATH = Path("tests/fixtures/sample_yoyaku_modern.html")

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
    ping_channel=False,
    cooldown_minutes=180,
    bonus_single_delta=2,
    bonus_ratio_threshold=0.5,
    ignore_older_than=1,
    notify_from_today=1,
    rollover_hours=dict(DEFAULT_ROLLOVER_HOURS),
    mask_level=1,
    robots_enforce=False,
    ua_contact=None,
)


def test_parse_day_entries_counts_and_single_logic():
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    entries = parse_day_entries(html, settings=BASE_SETTINGS, reference_date=date(2024, 1, 15))

    assert [entry.day_of_month for entry in entries] == [1, 2, 3, 4, 5, 6, 7]

    day1 = entries[0]
    assert day1.male == 1
    assert day1.female == 2  # スタッフ行もカウントされる
    assert day1.single_female == 1
    assert day1.dow_en == "Mon"  # 2024-01-01 is Monday (rolled to business label)
    assert day1.business_day.isoformat().endswith("-01")

    day2 = entries[1]
    assert day2.female == 1
    assert day2.single_female == 1

    day4 = entries[3]
    assert day4.female == 5
    assert day4.single_female == 0

    day6 = entries[5]
    assert day6.single_female == 5
    assert day6.female == 5
    assert day6.male == 1
    assert day6.dow_en == "Sat"

    day7 = entries[6]
    assert day7.single_female == 5
    assert day7.female == 5
    assert day7.male == 0
    assert day7.dow_en == "Sun"


def test_parse_day_entries_modern_layout_with_explicit_dates():
    html = MODERN_FIXTURE_PATH.read_text(encoding="utf-8")
    entries = parse_day_entries(html, settings=BASE_SETTINGS, reference_date=date(2025, 11, 2))

    # The modern layout provides explicit ISO dates, so the parser should trust
    # them even when they stray outside the current month window.
    dates = [entry.business_day for entry in entries]
    assert date(2025, 11, 1) in dates
    assert date(2025, 11, 6) in dates
    assert date(2025, 12, 15) in dates

    december_entry = next(entry for entry in entries if entry.business_day == date(2025, 12, 15))
    assert december_entry.female == 3  # two symbols + solitary line
    assert december_entry.single_female == 1


@pytest.mark.parametrize(
    "reference_day, cell_day, expected",
    [
        (date(2024, 4, 5), 31, date(2024, 3, 31)),
        (date(2024, 4, 28), 1, date(2024, 5, 1)),
        (date(2024, 7, 15), 20, date(2024, 7, 20)),
        # Test case for the bug: Oct 27 should interpret "3" as Nov 3, not Oct 3
        (date(2025, 10, 27), 3, date(2025, 11, 3)),
        # Additional edge cases
        (date(2025, 10, 27), 28, date(2025, 10, 28)),  # Same month, close date
        (date(2025, 1, 5), 30, date(2024, 12, 30)),     # Early in month, high day -> previous month
    ],
)
def test_infer_entry_date(reference_day, cell_day, expected):
    assert infer_entry_date(cell_day, reference_day) == expected


def test_derive_business_day_rollover_rules():
    # Tuesday with cutoff 5 => before cutoff uses previous day
    rollover = dict(DEFAULT_ROLLOVER_HOURS)
    ts_before_cutoff = datetime(2024, 1, 9, 4, 0, tzinfo=JST)
    ts_after_cutoff = datetime(2024, 1, 9, 6, 0, tzinfo=JST)

    day_before = derive_business_day(ts_before_cutoff, rollover)
    day_after = derive_business_day(ts_after_cutoff, rollover)

    assert day_before == date(2024, 1, 8)
    assert day_after == date(2024, 1, 9)
