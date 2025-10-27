"""Tests for build_slack_payload helper functions."""

import pytest
from datetime import date
from summarize import (
    DailyRecord,
    _format_date_range,
    _format_latest_field,
    _format_top_days_section,
    _format_stats_field,
)


def test_format_date_range():
    start = date(2025, 1, 15)
    end = date(2025, 1, 21)
    result = _format_date_range(start, end)
    assert "01/15" in result
    assert "01/21" in result
    assert "〜" in result


def test_format_latest_field():
    record = DailyRecord(
        business_day=date(2025, 1, 15),
        single_female=3,
        female=5,
        total=10,
        ratio=0.5,
    )
    lines = _format_latest_field(record)
    assert len(lines) == 3
    assert "01/15" in lines[0]
    assert "単女3" in lines[1]
    assert "50%" in lines[2]


def test_format_top_days_section_empty():
    lines = _format_top_days_section([])
    assert lines == ["• 該当なし"]


def test_format_top_days_section_with_records():
    record = DailyRecord(
        business_day=date(2025, 1, 15),
        single_female=3,
        female=5,
        total=10,
        ratio=0.5,
    )
    lines = _format_top_days_section([record])
    assert len(lines) == 1
    assert "単女3" in lines[0]
    assert "女5/全10" in lines[0]


def test_format_stats_field():
    stats_single = {"average": 3.5, "median": 3.0, "max": 5.0}
    stats_female = {"average": 5.2, "median": 5.0, "max": 8.0}
    stats_ratio = {"average": 52.3, "median": 50.0, "max": 60.0}

    lines = _format_stats_field(stats_single, stats_female, stats_ratio)
    assert len(lines) == 3
    assert "平均" in lines[0]
    assert "中央値" in lines[1]
    assert "最大" in lines[2]
