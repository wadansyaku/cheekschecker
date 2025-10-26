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
    start_day = date(2024, 1, 8)
    days = [
        make_raw_entry(start_day + timedelta(days=i), single=3 + (i % 2), female=8 + i, male=4)
        for i in range(7)
    ]
    prev_days = [
        make_raw_entry(start_day - timedelta(days=7 - i), single=2 + (i % 2), female=6 + i, male=5)
        for i in range(7)
    ]
    raw_payload = {
        "period_label": "latest 7 days",
        "days": days,
        "previous_days": prev_days,
    }
    raw_path = tmp_path / "weekly_raw.json"
    raw_path.write_text(json.dumps(raw_payload), encoding="utf-8")

    dataset = load_raw_dataset(raw_path)
    context = build_summary_context("weekly", dataset)
    assert context is not None
    assert context.day_count == 7
    assert context.top_days[0].single_female >= context.top_days[-1].single_female

    masked = build_masked_summary(context, history_meta={"mask_level": 1})
    assert masked["status"] == "ok"
    assert masked["stats"]["single"]["average"] in {"3-4", "5-6", "7-8"}
    assert masked["top_days"][0]["ratio"] in {"40±", "50±", "60±", "70±", "80+%"}
    assert "月" in masked["weekday_profile"] or "火" in masked["weekday_profile"]

    payload, fallback = build_slack_payload(context, "週次サマリー")
    assert "blocks" in payload and payload["blocks"]
    assert "Hot day Top3" in payload["blocks"][3]["text"]["text"]
    assert "Cheekschecker 週次サマリー" in fallback


def test_build_summary_context_no_data() -> None:
    dataset = RawDataset(period_label="", current=[], previous=[])
    context = build_summary_context("weekly", dataset)
    assert context is None

    masked = build_masked_summary(context, history_meta={"mask_level": 1})
    assert masked["status"] == "no-data"


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
    dataset = RawDataset(period_label="tie-case", current=records, previous=[])

    context = build_summary_context("weekly", dataset)
    assert context is not None
    top_days = context.top_days

    assert top_days[0].business_day == base_day + timedelta(days=1)
    assert top_days[1].business_day == base_day
