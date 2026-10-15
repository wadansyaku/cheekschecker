import json
from pathlib import Path

import pytest

from scripts.ci.check_public_artifacts import validate_public_artifacts
from src import public_state


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_validate_public_artifacts_accepts_minimal_public_safe_files(tmp_path: Path) -> None:
    monitor = tmp_path / "monitor_state.json"
    history = tmp_path / "history_masked.json"
    summary = tmp_path / "summary_masked.json"
    _write(
        monitor,
        {
            "generated_at": None,
            "etag": None,
            "last_modified": None,
            "last_fetched_at": None,
            "warning_throttle": {},
            "days": {"2026-05-08": {"met": True, "stage": "initial", "last_notified_at": 1}},
        },
    )
    _write(
        history,
        {
            "generated_at": None,
            "mask_level": 1,
            "days": {
                "2026-05-08": {
                    "single": "3-4",
                    "female": "5-6",
                    "total": "10-19",
                    "ratio": "50±",
                }
            },
        },
    )
    _write(
        summary,
        {
            "weekly": {
                "generated_at": None,
                "mask_level": 1,
                "mode": "public-safe",
                "status": "no-data",
                "day_count": 0,
                "coverage": {
                    "current": {
                        "target_days": 0,
                        "observed_days": 0,
                        "raw_days": 0,
                        "masked_days": 0,
                        "missing_days": 0,
                    },
                    "previous": {
                        "target_days": 0,
                        "observed_days": 0,
                        "raw_days": 0,
                        "masked_days": 0,
                        "missing_days": 0,
                    },
                },
            }
        },
    )

    assert validate_public_artifacts((monitor, history, summary)) == []


def test_validate_public_artifacts_rejects_raw_keys_and_secrets(tmp_path: Path) -> None:
    monitor = tmp_path / "monitor_state.json"
    history = tmp_path / "history_masked.json"
    summary = tmp_path / "summary_masked.json"
    _write(
        monitor,
        {
            "days": {
                "2026-05-08": {
                    "met": True,
                    "stage": "initial",
                    "last_notified_at": 1,
                    "counts": {"female": 6},
                }
            }
        },
    )
    _write(
        history,
        {
            "mask_level": 1,
            "days": {
                "2026-05-08": {
                    "single": "3-4",
                    "female": "5-6",
                    "total": "10-19",
                    "ratio": "50±",
                    "female_raw": 6,
                }
            },
        },
    )
    _write(
        summary,
        {
            "weekly": {
                "mode": "public-safe",
                "status": "source-unavailable",
                "fetch_error": "https://example.com/?token=secret",
            }
        },
    )

    errors = validate_public_artifacts((monitor, history, summary))

    assert any("forbidden public artifact key" in error for error in errors)
    assert any("not normalized" in error for error in errors)


@pytest.mark.parametrize("unsafe_label", ["Alice", "女性13人", "13/27", "50.25 %"])
def test_public_artifact_checker_rejects_disguised_raw_history_labels(
    tmp_path: Path, unsafe_label: str,
) -> None:
    history = tmp_path / "history_masked.json"
    _write(
        history,
        {"days": {"2026-05-15": {
            "single": "3-4", "female": unsafe_label, "total": "10-19", "ratio": "50±",
        }}},
    )

    errors = validate_public_artifacts((history,))

    assert any("not normalized by sanitize_masked_days" in error for error in errors)


@pytest.mark.parametrize("unsafe_field", ["stat", "day_label", "weekday"])
def test_public_artifact_checker_rejects_free_text_summary_fields(
    tmp_path: Path, unsafe_field: str,
) -> None:
    summary = tmp_path / "summary_masked.json"
    entry = {"single": "3-4", "female": "5-6", "total": "10-19", "ratio": "50±"}
    payload = public_state.sanitize_summary_store({"weekly": {
        "stats": {"female": {"average": "-"}},
        "top_days": [{"label": "05/15(月)", **entry}],
        "weekday_profile": {"月": entry},
    }})
    if unsafe_field == "stat":
        payload["weekly"]["stats"]["female"]["average"] = "女性13人"
    elif unsafe_field == "day_label":
        payload["weekly"]["top_days"][0]["label"] = "Alice 05/15(月)"
    else:
        payload["weekly"]["weekday_profile"]["Alice"] = entry
    _write(summary, payload)

    errors = validate_public_artifacts((summary,))

    assert any("not normalized by sanitize_summary_store" in error for error in errors)
