from datetime import date

from src.public_summary import (
    CoverageWindow,
    RawDataset,
    SummaryContext,
    SummaryRecord,
    build_placeholder_summary_payload,
    build_slack_payload,
    build_summary_context,
    raw_dataset_from_dict,
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


def test_raw_dataset_from_dict_tolerates_missing_record_lists() -> None:
    dataset = raw_dataset_from_dict(
        {
            "period_label": "broken",
            "window_days": "bad",
            "logical_today": "2025-01-21",
            "days": None,
            "previous_days": {"not": "a-list"},
            "fetch_status": "Partial",
            "fetch_error": 42,
        }
    )

    assert dataset.current == []
    assert dataset.previous == []
    assert dataset.window_days == 0
    assert dataset.fetch_status == "partial"
    assert dataset.fetch_error == "42"


def test_build_summary_context_ignores_unavailable_source_even_with_rows() -> None:
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
        fetch_status="partial",
    )

    assert build_summary_context("weekly", dataset, {"mask_level": 1, "days": {}}) is None


def test_build_summary_context_tolerates_missing_history_meta() -> None:
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

    context = build_summary_context("weekly", dataset, None)  # type: ignore[arg-type]

    assert context is not None
    assert context.coverage_current.raw_days == 1


def test_build_slack_payload_tolerates_partial_context_fields() -> None:
    target_day = date(2025, 1, 21)
    record = SummaryRecord(
        business_day=target_day,
        single_value=3.0,
        female_value=5.0,
        total_value=10.0,
        ratio_value=0.5,
        single_label="3-4",
        female_label="5-6",
        total_label="10-19",
        ratio_label="50±",
        source="raw",
    )
    context = SummaryContext(
        period_key="weekly",
        period_label="latest 7 days",
        period_start=target_day,
        period_end=target_day,
        current=[record],
        previous=[],
        coverage_current=CoverageWindow(
            target_days=7,
            observed_days=1,
            raw_days=1,
            masked_days=0,
            missing_days=6,
        ),
        coverage_previous=CoverageWindow(
            target_days=7,
            observed_days=0,
            raw_days=0,
            masked_days=0,
            missing_days=7,
        ),
        stats={},
        top_days=[record],
        weekday_profile={target_day.weekday(): {"single": "3-4"}},
        trend={"single": "sideways"},
    )

    payload, fallback, sections = build_slack_payload(context, "週次サマリー")

    assert payload["text"] == fallback
    assert "単女-" in fallback
    assert "単女? / 女? / 比率?" in fallback
    assert any(section[0] == "傾向・曜日・カバレッジ" for section in sections)


def test_build_slack_payload_uses_placeholder_for_empty_context() -> None:
    target_day = date(2025, 1, 21)
    context = SummaryContext(
        period_key="weekly",
        period_label="latest 7 days",
        period_start=target_day,
        period_end=target_day,
        current=[],
        previous=[],
        coverage_current=CoverageWindow(7, 0, 0, 0, 7),
        coverage_previous=CoverageWindow(7, 0, 0, 0, 7),
        stats={},
        top_days=[],
        weekday_profile={},
        trend={},
    )

    payload, fallback, sections = build_slack_payload(context, "週次サマリー")

    assert "集計対象なし" in fallback
    assert payload["blocks"][0]["type"] == "header"
    assert sections[0][0] == "最新観測"
