#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import public_state


PUBLIC_ARTIFACTS = (
    Path("monitor_state.json"),
    Path("history_masked.json"),
    Path("summary_masked.json"),
)
FORBIDDEN_KEYS = {
    "counts",
    "raw_counts",
    "male",
    "female_raw",
    "single_female",
    "exact_ratio",
    "fetch_error",
    "raw_error",
}
FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"https://hooks\.slack(?:-gov)?\.com/services/", re.IGNORECASE),
    re.compile(r"(?i)(token|secret|password|api[_-]?key)=([^&\s]+)"),
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk(value: Any, *, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_KEYS:
                errors.append(f"{child_path} uses forbidden public artifact key")
            errors.extend(_walk(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_walk(child, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(value):
                errors.append(f"{path} contains sensitive-looking text")
    return errors


def validate_public_artifacts(paths: tuple[Path, ...] = PUBLIC_ARTIFACTS) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if not path.exists():
            errors.append(f"{path} is missing")
            continue
        try:
            data = _load_json(path)
        except json.JSONDecodeError as exc:
            errors.append(f"{path} is not valid JSON: {exc}")
            continue

        errors.extend(f"{path}:{error}" for error in _walk(data))

        if path.name == "history_masked.json":
            expected_days = public_state.sanitize_masked_days(data.get("days"))
            if data.get("days") != expected_days:
                errors.append(f"{path} is not normalized by sanitize_masked_days")
        elif path.name == "summary_masked.json":
            expected = public_state.sanitize_summary_store(data)
            if data != expected:
                errors.append(f"{path} is not normalized by sanitize_summary_store")
        elif path.name == "monitor_state.json":
            unexpected = set(data) - {
                "generated_at",
                "etag",
                "last_modified",
                "last_fetched_at",
                "warning_throttle",
                "days",
            }
            if unexpected:
                errors.append(f"{path} has unexpected top-level keys: {sorted(unexpected)}")
            for day_key, entry in data.get("days", {}).items():
                if set(entry) - {"met", "stage", "last_notified_at"}:
                    errors.append(f"{path}.days.{day_key} has non public-safe keys")
    return errors


def main() -> int:
    errors = validate_public_artifacts()
    if errors:
        print("Public artifact validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Public artifact validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
