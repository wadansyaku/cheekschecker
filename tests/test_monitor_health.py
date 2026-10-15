from datetime import UTC, datetime

from scripts.ci.report_monitor_health import build_report

NOW = datetime(2026, 10, 15, 6, tzinfo=UTC)


def test_diagnostics_do_not_fill_scheduled_gaps_or_refresh_the_source():
    report = build_report(
        {
            "workflow_runs": [
                {
                    "event": "schedule",
                    "created_at": "2026-10-15T00:00:00Z",
                    "conclusion": "success",
                },
                {
                    "event": "workflow_dispatch",
                    "created_at": "2026-10-15T02:00:00Z",
                    "conclusion": "success",
                },
                {
                    "event": "schedule",
                    "created_at": "2026-10-15T05:00:00Z",
                    "conclusion": "success",
                },
            ]
        },
        {"last_fetched_at": "2026-10-15T00:00:00Z"},
        now=NOW,
    )
    assert report["scheduled_run_count"] == 2
    assert report["scheduled_gap_minutes"]["median"] == 300
    assert report["successful_fetch_age_minutes"] == 360
    assert report["current_check_status"] == "overdue"


def test_header_check_freshness_is_distinct_from_body_freshness():
    report = build_report(
        {},
        {
            "last_checked_at": "2026-10-15T05:58:00Z",
            "last_fetched_at": "2026-10-15T04:00:00Z",
        },
        now=NOW,
    )
    assert report["current_check_status"] == "recent"
    assert report["successful_check_age_minutes"] == 2
    assert report["successful_fetch_age_minutes"] == 120
    assert report["historical_schedule_status"] == "unknown"


def test_missing_invalid_and_future_measurements_are_unknown():
    for state in (
        {},
        {"last_checked_at": "bad"},
        {"last_checked_at": "2026-10-16T00:00:00Z"},
    ):
        report = build_report({}, state, now=NOW)
        assert report["successful_check_age_minutes"] is None
        assert report["successful_fetch_age_minutes"] is None
        assert report["scheduled_gap_minutes"]["median"] is None
        assert report["current_check_status"] == "unknown"
