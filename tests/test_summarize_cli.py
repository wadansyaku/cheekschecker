import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import summarize  # noqa: E402
from src.public_summary import RawDataset  # noqa: E402


def test_run_summary_handles_source_unavailable(monkeypatch) -> None:
    events = {"saved": [], "slack": [], "summary": []}

    monkeypatch.setattr("summarize.load_masked_history", lambda path: {"mask_level": 1, "days": {}})
    monkeypatch.setattr(
        "summarize.load_raw_dataset",
        lambda path: RawDataset(
            period_label="過去7日",
            window_days=7,
            logical_today=date(2026, 4, 23),
            current=[],
            previous=[],
            fetch_status="unavailable",
            fetch_error="connect timeout",
        ),
    )
    monkeypatch.setattr("summarize.load_summary_store", lambda path: {"weekly": {"mode": "public-safe"}})
    monkeypatch.setattr(
        "summarize.save_summary_store",
        lambda path, data: events["saved"].append((path, data)),
    )
    monkeypatch.setattr(
        "summarize.append_step_summary",
        lambda title, sections, fallback: events["summary"].append((title, sections, fallback)),
    )
    monkeypatch.setattr(
        "summarize.send_slack_message",
        lambda webhook, payload, fallback: events["slack"].append((webhook, payload, fallback)),
    )

    args = argparse.Namespace(
        period="weekly",
        raw_data=Path("weekly_summary_raw.json"),
        history=Path("history_masked.json"),
        output=Path("summary_masked.json"),
        slack_webhook="https://hooks.slack.test/services/example",
    )

    assert summarize.run_summary(args) == 0
    assert events["saved"] == []
    assert len(events["summary"]) == 1
    assert "外部サイト取得失敗" in events["summary"][0][2]
    assert len(events["slack"]) == 1
