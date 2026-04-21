from datetime import date

from src.public_summary import (
    RawDataset,
    build_placeholder_summary_payload,
    build_slack_payload,
    build_summary_context,
)
from summarize import DailyRecord


def test_build_placeholder_summary_payload() -> None:
    payload, fallback, sections = build_placeholder_summary_payload(
        "Cheekschecker 週次サマリー",
        "No data for this period / 集計対象なし",
    )
    assert "blocks" in payload
    assert "Cheekschecker 週次サマリー" in fallback
    assert sections[0][0] == "最新観測"
    assert "public-safe approximation" in payload["blocks"][1]["elements"][0]["text"]


def test_build_slack_payload_includes_coverage_line() -> None:
    dataset = RawDataset(
        period_label="latest 7 days",
        window_days=7,
        logical_today=date(2025, 1, 21),
        current=[
            DailyRecord(
                business_day=date(2025, 1, 21),
                single_female=3,
                female=5,
                total=10,
                ratio=0.5,
            )
        ],
        previous=[],
    )
    history_meta = {
        "mask_level": 1,
        "days": {
            "2025-01-15": {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"},
            "2025-01-16": {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"},
            "2025-01-17": {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"},
            "2025-01-18": {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"},
            "2025-01-19": {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"},
            "2025-01-20": {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"},
        },
    }
    context = build_summary_context("weekly", dataset, history_meta)
    assert context is not None

    payload, fallback, sections = build_slack_payload(context, "週次サマリー")
    assert "カバレッジ" in fallback
    assert any(section[0] == "傾向・曜日・カバレッジ" for section in sections)
    assert "Top3 好条件日" in payload["blocks"][3]["text"]["text"]
