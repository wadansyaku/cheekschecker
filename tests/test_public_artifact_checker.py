import json
from pathlib import Path

from scripts.ci.check_public_artifacts import validate_public_artifacts


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
