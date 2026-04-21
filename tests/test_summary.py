import json
from datetime import date, timedelta
from pathlib import Path

from summarize import (
    DailyRecord,
    RawDataset,
    build_masked_summary,
    build_summary_context,
    build_slack_payload,
    load_raw_dataset,
)


def make_raw_entry(day: date, single: int, female: int, male: int) -> dict:
    total = female + male
    ratio = female / total if total else 0
    return {
        "date": day.isoformat(),
        "single_female": single,
        "female": female,
        "male": male,
        "total": total,
        "ratio": ratio,
    }


def test_load_and_build_summary(tmp_path: Path) -> None:
    logical_today = date(2024, 1, 14)
    start_day = date(2024, 1, 8)
    days = [
        make_raw_entry(start_day + timedelta(days=i), single=3 + (i % 2), female=8 + i, male=4)
        for i in range(5)
    ]
    prev_days = [make_raw_entry(date(2024, 1, 7), single=2, female=6, male=5)]
    raw_payload = {
        "period_label": "latest 7 days",
        "window_days": 7,
        "logical_today": logical_today.isoformat(),
        "days": days,
        "previous_days": prev_days,
    }
    raw_path = tmp_path / "weekly_raw.json"
    raw_path.write_text(json.dumps(raw_payload), encoding="utf-8")

    dataset = load_raw_dataset(raw_path)
    history_meta = {
        "mask_level": 1,
        "days": {
            "2024-01-13": {"single": "3-4", "female": "7-8", "total": "10-19", "ratio": "50±"},
            "2024-01-14": {"single": "5-6", "female": "9+", "total": "20-29", "ratio": "50±"},
            "2024-01-01": {"single": "1", "female": "3-4", "total": "10-19", "ratio": "40±"},
            "2024-01-02": {"single": "2", "female": "5-6", "total": "10-19", "ratio": "40±"},
            "2024-01-03": {"single": "2", "female": "5-6", "total": "10-19", "ratio": "40±"},
            "2024-01-04": {"single": "3-4", "female": "7-8", "total": "20-29", "ratio": "40±"},
            "2024-01-05": {"single": "3-4", "female": "7-8", "total": "20-29", "ratio": "40±"},
            "2024-01-06": {"single": "3-4", "female": "7-8", "total": "20-29", "ratio": "50±"},
        },
    }
    context = build_summary_context("weekly", dataset, history_meta)
    assert context is not None
    assert context.day_count == 7
    assert context.coverage_current.raw_days == 5
    assert context.coverage_current.masked_days == 2
    assert context.coverage_previous.raw_days == 1
    assert context.coverage_previous.masked_days == 6
    assert context.top_days[0].single_value >= context.top_days[-1].single_value

    masked = build_masked_summary(context, history_meta={"mask_level": 1})
    assert masked["status"] == "ok"
    assert masked["mode"] == "public-safe"
    assert masked["stats"]["single"]["average"] in {"3-4", "5-6", "7-8"}
    assert masked["top_days"][0]["ratio"] in {"40±", "50±", "60±", "70±", "80+%"}
    assert "月" in masked["weekday_profile"] or "火" in masked["weekday_profile"]
    assert masked["coverage"]["current"]["masked_days"] == 2

    payload, fallback, step_sections = build_slack_payload(context, "週次サマリー")
    assert "blocks" in payload and payload["blocks"]
    assert "Top3 好条件日" in payload["blocks"][3]["text"]["text"]
    assert "public-safe approximation" in fallback
    assert "Cheekschecker 週次サマリー" in fallback
    assert len(step_sections) == 4


def test_build_summary_context_no_data() -> None:
    dataset = RawDataset(
        period_label="",
        window_days=7,
        logical_today=date(2024, 1, 7),
        current=[],
        previous=[],
    )
    context = build_summary_context("weekly", dataset, {"mask_level": 1, "days": {}})
    assert context is None

    masked = build_masked_summary(context, history_meta={"mask_level": 1})
    assert masked["status"] == "no-data"
    assert masked["mode"] == "public-safe"


def test_top_days_prefers_latest_when_tied() -> None:
    base_day = date(2024, 2, 1)
    records = [
        DailyRecord(
            business_day=base_day,
            single_female=5,
            female=10,
            total=20,
            ratio=0.5,
        ),
        DailyRecord(
            business_day=base_day + timedelta(days=1),
            single_female=5,
            female=10,
            total=20,
            ratio=0.5,
        ),
        DailyRecord(
            business_day=base_day - timedelta(days=1),
            single_female=4,
            female=8,
            total=18,
            ratio=0.4444,
        ),
    ]
    dataset = RawDataset(
        period_label="tie-case",
        window_days=3,
        logical_today=base_day + timedelta(days=1),
        current=records,
        previous=[],
    )

    context = build_summary_context("weekly", dataset, {"mask_level": 1, "days": {}})
    assert context is not None
    top_days = context.top_days

    assert top_days[0].business_day == base_day + timedelta(days=1)
    assert top_days[1].business_day == base_day
