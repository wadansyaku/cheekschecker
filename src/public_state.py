"""Public-safe persistence helpers for monitor and summary artifacts."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo


LOGGER = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
MONITOR_STATE_PATH = Path("monitor_state.json")
LEGACY_STATE_PATH = Path("state.json")
HISTORY_MASKED_PATH = Path("history_masked.json")
SUMMARY_MASKED_PATH = Path("summary_masked.json")

DATE_KEY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
VALID_STAGES = {"none", "initial", "bonus"}


def default_monitor_state() -> Dict[str, Any]:
    return {
        "generated_at": None,
        "etag": None,
        "last_modified": None,
        "days": {},
    }


def _read_json_file(path: Path, *, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        LOGGER.warning("Failed to parse %s: %s", path, exc)
        return dict(default)
    if isinstance(data, dict):
        return data
    LOGGER.warning("%s did not contain a JSON object; resetting", path)
    return dict(default)


def _write_json_atomic(path: Path, data: Dict[str, Any], *, sort_keys: bool = False) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort_keys),
        encoding="utf-8",
    )
    tmp.replace(path)


def _coerce_stage(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in VALID_STAGES:
            return lowered
    return "none"


def _coerce_last_notified(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _normalize_day_entry(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    last_notified = _coerce_last_notified(value.get("last_notified_at"))
    return {
        "met": bool(value.get("met")),
        "stage": _coerce_stage(value.get("stage")),
        "last_notified_at": last_notified,
    }


def migrate_legacy_state(
    legacy_state: Dict[str, Any],
    *,
    reference_date: date,
    legacy_day_resolver: Callable[[int, date], date],
) -> Dict[str, Any]:
    migrated = default_monitor_state()
    migrated["etag"] = legacy_state.get("etag")
    migrated["last_modified"] = legacy_state.get("last_modified")
    days = legacy_state.get("days")
    if not isinstance(days, dict):
        return migrated

    normalized: Dict[str, Any] = {}
    for raw_key, raw_value in days.items():
        key: Optional[str] = None
        if isinstance(raw_key, str) and len(raw_key) == 10 and raw_key[4] == "-" and raw_key[7] == "-":
            key = raw_key
        elif isinstance(raw_key, str) and raw_key.isdigit():
            key = legacy_day_resolver(int(raw_key), reference_date).isoformat()
        else:
            LOGGER.debug("Dropping unsupported legacy monitor_state key=%s", raw_key)
        if key is None:
            continue
        entry = _normalize_day_entry(raw_value)
        if entry is None:
            continue
        normalized[key] = entry
    migrated["days"] = normalized
    return migrated


def load_monitor_state(
    *,
    reference_date: date,
    legacy_day_resolver: Callable[[int, date], date],
    path: Path = MONITOR_STATE_PATH,
    legacy_path: Path = LEGACY_STATE_PATH,
) -> Dict[str, Any]:
    if path.exists():
        raw = _read_json_file(path, default=default_monitor_state())
    elif legacy_path.exists():
        LOGGER.info("Migrating legacy state from %s to %s", legacy_path, path)
        raw = migrate_legacy_state(
            _read_json_file(legacy_path, default=default_monitor_state()),
            reference_date=reference_date,
            legacy_day_resolver=legacy_day_resolver,
        )
    else:
        raw = default_monitor_state()

    days = raw.get("days")
    normalized_days: Dict[str, Any] = {}
    if isinstance(days, dict):
        for key, value in days.items():
            if not isinstance(key, str):
                continue
            if len(key) != 10 or key[4] != "-" or key[7] != "-":
                continue
            entry = _normalize_day_entry(value)
            if entry is None:
                continue
            normalized_days[key] = entry

    return {
        "generated_at": raw.get("generated_at"),
        "etag": raw.get("etag"),
        "last_modified": raw.get("last_modified"),
        "days": normalized_days,
    }


def save_monitor_state(state: Dict[str, Any], *, path: Path = MONITOR_STATE_PATH) -> None:
    payload = {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "etag": state.get("etag"),
        "last_modified": state.get("last_modified"),
        "days": {},
    }
    days = state.get("days")
    if isinstance(days, dict):
        for key, value in days.items():
            if not isinstance(key, str):
                continue
            entry = _normalize_day_entry(value)
            if entry is None:
                continue
            payload["days"][key] = entry
    _write_json_atomic(path, payload, sort_keys=True)


def load_masked_history(path: Path = HISTORY_MASKED_PATH, *, default_mask_level: int = 1) -> Dict[str, Any]:
    raw = _read_json_file(
        path,
        default={"days": {}, "mask_level": default_mask_level, "generated_at": None},
    )
    days = raw.get("days")
    if not isinstance(days, dict):
        days = {}
    return {
        "generated_at": raw.get("generated_at"),
        "mask_level": raw.get("mask_level", default_mask_level),
        "days": days,
    }


def save_masked_history(data: Dict[str, Any], path: Path = HISTORY_MASKED_PATH) -> None:
    payload = {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "mask_level": data.get("mask_level", 1),
        "days": data.get("days", {}),
    }
    _write_json_atomic(path, payload, sort_keys=True)


def load_summary_store(path: Path = SUMMARY_MASKED_PATH) -> Dict[str, Any]:
    return _read_json_file(path, default={})


def save_summary_store(path: Path, data: Dict[str, Any]) -> None:
    _write_json_atomic(path, data, sort_keys=True)


__all__ = [
    "HISTORY_MASKED_PATH",
    "JST",
    "LEGACY_STATE_PATH",
    "MONITOR_STATE_PATH",
    "SUMMARY_MASKED_PATH",
    "default_monitor_state",
    "load_masked_history",
    "load_monitor_state",
    "load_summary_store",
    "migrate_legacy_state",
    "save_masked_history",
    "save_monitor_state",
    "save_summary_store",
]
