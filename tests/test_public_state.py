import json
from datetime import date
from pathlib import Path

from src import public_state


def _legacy_resolver(day: int, reference_date: date) -> date:
    return date(reference_date.year, reference_date.month, min(day, 28))


def test_load_monitor_state_migrates_legacy_counts(tmp_path: Path) -> None:
    legacy_path = tmp_path / "state.json"
    legacy_path.write_text(
        json.dumps(
            {
                "etag": "legacy-etag",
                "last_modified": "legacy-last-modified",
                "days": {
                    "15": {
                        "met": True,
                        "stage": "bonus",
                        "last_notified_at": 1234,
                        "counts": {"female": 8, "single_female": 3, "total": 12},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = public_state.load_monitor_state(
        reference_date=date(2024, 1, 20),
        legacy_day_resolver=_legacy_resolver,
        path=tmp_path / "monitor_state.json",
        legacy_path=legacy_path,
    )

    assert state["etag"] == "legacy-etag"
    assert state["last_modified"] == "legacy-last-modified"
    assert state["days"]["2024-01-15"] == {
        "met": True,
        "stage": "bonus",
        "last_notified_at": 1234,
    }
    assert "counts" not in state["days"]["2024-01-15"]


def test_save_monitor_state_strips_raw_counts(tmp_path: Path) -> None:
    path = tmp_path / "monitor_state.json"
    public_state.save_monitor_state(
        {
            "etag": "abc",
            "last_modified": "def",
            "last_fetched_at": "2024-01-15T10:00:00+09:00",
            "days": {
                "2024-01-15": {
                    "met": True,
                    "stage": "initial",
                    "last_notified_at": 55,
                    "counts": {"female": 9, "single_female": 5, "total": 14},
                }
            },
        },
        path=path,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["etag"] == "abc"
    assert saved["last_fetched_at"] == "2024-01-15T10:00:00+09:00"
    assert saved["days"]["2024-01-15"] == {
        "met": True,
        "stage": "initial",
        "last_notified_at": 55,
    }


def test_monitor_state_drops_invalid_day_keys(tmp_path: Path) -> None:
    path = tmp_path / "monitor_state.json"
    public_state.save_monitor_state(
        {
            "days": {
                "2024-01-15": {"met": True, "stage": "initial", "last_notified_at": 55},
                "2024-99-99": {"met": True, "stage": "bonus", "last_notified_at": 56},
                "not-a-date": {"met": True, "stage": "bonus", "last_notified_at": 57},
            }
        },
        path=path,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert list(saved["days"]) == ["2024-01-15"]
    loaded = public_state.load_monitor_state(
        reference_date=date(2024, 1, 20),
        legacy_day_resolver=_legacy_resolver,
        path=path,
        legacy_path=tmp_path / "state.json",
    )
    assert list(loaded["days"]) == ["2024-01-15"]


def test_save_monitor_state_sanitizes_warning_throttle(tmp_path: Path) -> None:
    path = tmp_path / "monitor_state.json"
    public_state.save_monitor_state(
        {
            "warning_throttle": {
                "monitor_fetch_failure": {
                    "last_seen_at": "2024-01-15T10:00:00+09:00",
                    "last_warned_at": "2024-01-15T10:00:00+09:00",
                    "consecutive_runs": "3",
                    "suppressed_runs": 2,
                    "last_category": "fetch_unavailable",
                    "raw_error": "connect timeout",
                },
                "unknown_warning": {
                    "last_seen_at": "2024-01-15T10:00:00+09:00",
                    "raw_error": "should not survive",
                },
                "weekly_fetch_failure": {
                    "last_seen_at": "not-a-date",
                    "last_warned_at": "2024-01-15T10:00:00",
                    "consecutive_runs": -4,
                    "suppressed_runs": "bad",
                    "last_category": "HTTPConnectionPool(host='example')",
                },
            }
        },
        path=path,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved["warning_throttle"]) == {
        "monitor_fetch_failure",
        "weekly_fetch_failure",
        "monthly_fetch_failure",
    }
    assert saved["warning_throttle"]["monitor_fetch_failure"] == {
        "last_seen_at": "2024-01-15T10:00:00+09:00",
        "last_warned_at": "2024-01-15T10:00:00+09:00",
        "consecutive_runs": 3,
        "suppressed_runs": 2,
        "last_category": "fetch_unavailable",
    }
    assert saved["warning_throttle"]["weekly_fetch_failure"] == {
        "last_seen_at": None,
        "last_warned_at": "2024-01-15T10:00:00+09:00",
        "consecutive_runs": 0,
        "suppressed_runs": 0,
        "last_category": None,
    }


def test_masked_history_sanitizer_drops_raw_like_values(tmp_path: Path) -> None:
    path = tmp_path / "history_masked.json"
    public_state.save_masked_history(
        {
            "mask_level": 1,
            "days": {
                "2024-01-15": {
                    "single": "3-4",
                    "female": "5-6",
                    "total": "10-19",
                    "ratio": "50±",
                    "female_raw": 6,
                },
                "2024-01-16": {
                    "single": "3",
                    "female": 5,
                    "total": "10",
                    "ratio": "0.42",
                },
                "not-a-date": {
                    "single": "3-4",
                    "female": "5-6",
                    "total": "10-19",
                    "ratio": "50±",
                },
            },
        },
        path=path,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert list(saved["days"]) == ["2024-01-15"]
    assert saved["days"]["2024-01-15"] == {
        "single": "3-4",
        "female": "5-6",
        "total": "10-19",
        "ratio": "50±",
    }


def test_summary_store_sanitizer_keeps_public_safe_shape(tmp_path: Path) -> None:
    path = tmp_path / "summary_masked.json"
    public_state.save_summary_store(
        path,
        {
            "weekly": {
                "generated_at": "2024-01-15T10:00:00+09:00",
                "mask_level": 1,
                "mode": "public-safe",
                "status": "ok",
                "period_start": "2024-01-09",
                "period_end": "2024-01-15",
                "day_count": 7,
                "stats": {
                    "female": {"average": "5-6", "median": "5-6", "max": "9+"},
                    "ratio": {"average": "50±", "median": "50±", "max": "80+%"},
                    "raw": {"average": "0.534"},
                },
                "top_days": [
                    {
                        "label": "15日(月)",
                        "single": "3-4",
                        "female": "5-6",
                        "total": "10-19",
                        "ratio": "50±",
                        "source": "raw",
                        "exact_ratio": 0.534,
                    },
                    {
                        "label": "bad",
                        "single": "3",
                        "female": "5",
                        "total": "10",
                        "ratio": "0.534",
                    },
                ],
                "trend": {"single": "flat", "female": "up", "ratio": "down"},
                "weekday_profile": {
                    "月": {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"}
                },
                "coverage": {
                    "current": {"target_days": 7, "observed_days": 7},
                    "previous": {"target_days": 7, "observed_days": 3},
                },
                "raw_counts": {"female": 6},
            },
            "monthly": {
                "generated_at": "2024-01-15T10:00:00+09:00",
                "mask_level": 1,
                "mode": "public-safe",
                "status": "source-unavailable",
                "coverage": {},
            },
            "debug": {"raw": True},
        },
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved) == {"weekly", "monthly"}
    assert "raw_counts" not in saved["weekly"]
    assert "raw" not in saved["weekly"]["stats"]
    assert len(saved["weekly"]["top_days"]) == 1
    assert "exact_ratio" not in saved["weekly"]["top_days"][0]
    assert saved["monthly"]["status"] == "source-unavailable"
