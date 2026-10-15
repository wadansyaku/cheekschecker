#!/usr/bin/env python3
"""Report scheduling gaps separately from successful checks and source fetches."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _age(value: Any, now: datetime) -> float | None:
    parsed = _time(value)
    if parsed is None or parsed > now:
        return None
    return round((now - parsed).total_seconds() / 60, 2)


def build_report(
    runs: dict[str, Any], state: dict[str, Any], *, now: datetime
) -> dict[str, Any]:
    """Use only coarse operational metadata, never observation values or text."""
    scheduled = [
        run
        for run in runs.get("workflow_runs", [])
        if isinstance(run, dict) and run.get("event") == "schedule"
    ]
    starts = sorted(
        time for run in scheduled if (time := _time(run.get("created_at"))) is not None
    )
    gaps = [(end - start).total_seconds() / 60 for start, end in pairwise(starts)]
    checked = state.get("last_checked_at") or state.get("last_fetched_at")
    check_age = _age(checked, now)
    return {
        "generated_at": now.isoformat(),
        "scheduled_run_count": len(scheduled),
        "scheduled_conclusions": dict(
            Counter(str(run.get("conclusion") or "unfinished") for run in scheduled)
        ),
        "scheduled_created_window": (
            [time.isoformat() for time in (starts[0], starts[-1])] if starts else None
        ),
        "scheduled_gap_minutes": {
            "interval_count": len(gaps),
            "median": round(statistics.median(gaps), 2) if gaps else None,
            "maximum": round(max(gaps), 2) if gaps else None,
            "over_30_minutes": sum(gap > 30 for gap in gaps),
        },
        "successful_check_age_minutes": check_age,
        "successful_fetch_age_minutes": _age(state.get("last_fetched_at"), now),
        "current_check_status": (
            "unknown"
            if check_age is None
            else "overdue" if check_age > 30 else "recent"
        ),
        "historical_schedule_status": (
            "unknown"
            if not gaps
            else "delayed" if statistics.median(gaps) > 30 else "regular"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=Path, required=True, help="GitHub workflow runs API JSON"
    )
    parser.add_argument(
        "--state", type=Path, required=True, help="Public monitor state JSON"
    )
    parser.add_argument(
        "--now", help="Timezone-aware ISO time for reproducible analysis"
    )
    args = parser.parse_args()
    now = _time(args.now) if args.now else datetime.now(tz=UTC)
    if now is None:
        parser.error("--now must be timezone-aware ISO time")
    report = build_report(
        json.loads(args.runs.read_text(encoding="utf-8")),
        json.loads(args.state.read_text(encoding="utf-8")),
        now=now,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
