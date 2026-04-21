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
    assert saved["days"]["2024-01-15"] == {
        "met": True,
        "stage": "initial",
        "last_notified_at": 55,
    }
