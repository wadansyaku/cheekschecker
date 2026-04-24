"""Public-safe persistence helpers for monitor and summary artifacts."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from src.masking import DEFAULT_MASKING_CONFIG


LOGGER = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
MONITOR_STATE_PATH = Path("monitor_state.json")
LEGACY_STATE_PATH = Path("state.json")
HISTORY_MASKED_PATH = Path("history_masked.json")
SUMMARY_MASKED_PATH = Path("summary_masked.json")

DATE_KEY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
DATE_KEY_RE = re.compile(DATE_KEY_PATTERN)
VALID_STAGES = {"none", "initial", "bonus"}
MASK_ENTRY_KEYS = ("single", "female", "total", "ratio")
SUMMARY_PERIOD_KEYS = {"weekly", "monthly"}
SUMMARY_STATUS_VALUES = {"ok", "no-data"}
SUMMARY_TREND_VALUES = {"up", "down", "flat", "unknown"}
SUMMARY_SOURCE_VALUES = {"raw", "masked"}
SUMMARY_METRIC_KEYS = ("single", "female", "total", "ratio")
SUMMARY_STAT_KEYS = ("average", "median", "max")
SUMMARY_COVERAGE_KEYS = (
    "target_days",
    "observed_days",
    "raw_days",
    "masked_days",
    "missing_days",
)
WARNING_THROTTLE_KEYS = (
    "monitor_fetch_failure",
    "weekly_fetch_failure",
    "monthly_fetch_failure",
)
WARNING_CATEGORY_VALUES = {"fetch_unavailable"}

DEFAULT_SAFE_MASK_LABELS = {
    label
    for bands in (
        DEFAULT_MASKING_CONFIG.count_bands,
        DEFAULT_MASKING_CONFIG.total_bands,
        DEFAULT_MASKING_CONFIG.ratio_bands,
    )
    for _, _, label in bands
}
for words in DEFAULT_MASKING_CONFIG.level2_words.values():
    DEFAULT_SAFE_MASK_LABELS.update(words)


def default_monitor_state() -> Dict[str, Any]:
    return {
        "generated_at": None,
        "etag": None,
        "last_modified": None,
        "last_fetched_at": None,
        "warning_throttle": default_warning_throttle_state(),
        "days": {},
    }


def default_warning_throttle_state() -> Dict[str, Dict[str, Any]]:
    return {
        key: {
            "last_seen_at": None,
            "last_warned_at": None,
            "consecutive_runs": 0,
            "suppressed_runs": 0,
            "last_category": None,
        }
        for key in WARNING_THROTTLE_KEYS
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


def _is_iso_date_key(value: Any) -> bool:
    if not isinstance(value, str) or not DATE_KEY_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _coerce_iso_datetime_text(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    else:
        parsed = parsed.astimezone(JST)
    return parsed.isoformat()


def _coerce_warning_category(value: Any) -> Optional[str]:
    text = _safe_text(value, max_length=40)
    if text in WARNING_CATEGORY_VALUES:
        return text
    return None


def _coerce_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _safe_text(value: Any, *, max_length: int = 80) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > max_length:
        return None
    if any(ord(ch) < 32 for ch in text):
        return None
    return text


def _looks_like_raw_numeric_label(text: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?%?", text))


def _sanitize_mask_label(value: Any) -> Optional[str]:
    text = _safe_text(value, max_length=32)
    if text is None:
        return None
    if text in DEFAULT_SAFE_MASK_LABELS:
        return text
    if _looks_like_raw_numeric_label(text):
        return None
    return text


def _sanitize_masked_entry(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    sanitized: Dict[str, str] = {}
    for key in MASK_ENTRY_KEYS:
        label = _sanitize_mask_label(value.get(key))
        if label is None:
            return None
        sanitized[key] = label
    return sanitized


def sanitize_masked_days(days: Any) -> Dict[str, Dict[str, str]]:
    sanitized: Dict[str, Dict[str, str]] = {}
    if not isinstance(days, dict):
        return sanitized
    for key, value in days.items():
        if not _is_iso_date_key(key):
            LOGGER.warning("Dropping invalid masked history date key=%s", key)
            continue
        entry = _sanitize_masked_entry(value)
        if entry is None:
            LOGGER.warning("Dropping non public-safe masked history entry for date=%s", key)
            continue
        sanitized[key] = entry
    return sanitized


def _sanitize_stats(value: Any) -> Dict[str, Dict[str, str]]:
    sanitized: Dict[str, Dict[str, str]] = {}
    if not isinstance(value, dict):
        return sanitized
    for metric in SUMMARY_METRIC_KEYS:
        raw_metric = value.get(metric)
        if not isinstance(raw_metric, dict):
            continue
        metric_payload: Dict[str, str] = {}
        for stat_key in SUMMARY_STAT_KEYS:
            label = _sanitize_mask_label(raw_metric.get(stat_key))
            if label is not None:
                metric_payload[stat_key] = label
        if metric_payload:
            sanitized[metric] = metric_payload
    return sanitized


def _sanitize_top_days(value: Any) -> list[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    sanitized: list[Dict[str, str]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        label = _safe_text(item.get("label"), max_length=40)
        if label is None:
            continue
        payload: Dict[str, str] = {"label": label}
        complete = True
        for metric in MASK_ENTRY_KEYS:
            metric_label = _sanitize_mask_label(item.get(metric))
            if metric_label is None:
                complete = False
                break
            payload[metric] = metric_label
        if not complete:
            continue
        source = _safe_text(item.get("source"), max_length=16)
        if source in SUMMARY_SOURCE_VALUES:
            payload["source"] = source
        sanitized.append(payload)
    return sanitized


def _sanitize_trend(value: Any) -> Dict[str, str]:
    sanitized: Dict[str, str] = {}
    if not isinstance(value, dict):
        return sanitized
    for metric in ("single", "female", "ratio"):
        raw = _safe_text(value.get(metric), max_length=16)
        if raw in SUMMARY_TREND_VALUES:
            sanitized[metric] = raw
    return sanitized


def _sanitize_weekday_profile(value: Any) -> Dict[str, Dict[str, str]]:
    sanitized: Dict[str, Dict[str, str]] = {}
    if not isinstance(value, dict):
        return sanitized
    for raw_key, raw_profile in value.items():
        key = _safe_text(raw_key, max_length=8)
        if key is None or not isinstance(raw_profile, dict):
            continue
        profile: Dict[str, str] = {}
        complete = True
        for metric in MASK_ENTRY_KEYS:
            label = _sanitize_mask_label(raw_profile.get(metric))
            if label is None:
                complete = False
                break
            profile[metric] = label
        if complete:
            sanitized[key] = profile
    return sanitized


def _sanitize_coverage_window(value: Any) -> Dict[str, int]:
    sanitized = {key: 0 for key in SUMMARY_COVERAGE_KEYS}
    if not isinstance(value, dict):
        return sanitized
    for key in SUMMARY_COVERAGE_KEYS:
        sanitized[key] = _coerce_nonnegative_int(value.get(key))
    return sanitized


def _sanitize_coverage(value: Any) -> Dict[str, Dict[str, int]]:
    if not isinstance(value, dict):
        value = {}
    return {
        "current": _sanitize_coverage_window(value.get("current")),
        "previous": _sanitize_coverage_window(value.get("previous")),
    }


def _sanitize_summary_payload(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    payload: Dict[str, Any] = {
        "generated_at": _coerce_iso_datetime_text(value.get("generated_at")),
        "mask_level": _coerce_nonnegative_int(value.get("mask_level", 1)),
        "mode": "public-safe",
        "status": "no-data",
    }

    status = _safe_text(value.get("status"), max_length=16)
    if status in SUMMARY_STATUS_VALUES:
        payload["status"] = status

    for key in ("period_start", "period_end"):
        raw = value.get(key)
        if _is_iso_date_key(raw):
            payload[key] = raw

    payload["day_count"] = _coerce_nonnegative_int(value.get("day_count"))

    stats = _sanitize_stats(value.get("stats"))
    if stats:
        payload["stats"] = stats

    top_days = _sanitize_top_days(value.get("top_days"))
    if top_days:
        payload["top_days"] = top_days

    trend = _sanitize_trend(value.get("trend"))
    if trend:
        payload["trend"] = trend

    weekday_profile = _sanitize_weekday_profile(value.get("weekday_profile"))
    if weekday_profile:
        payload["weekday_profile"] = weekday_profile

    payload["coverage"] = _sanitize_coverage(value.get("coverage"))
    return payload


def sanitize_summary_store(data: Any) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    if not isinstance(data, dict):
        return sanitized
    for period in SUMMARY_PERIOD_KEYS:
        payload = _sanitize_summary_payload(data.get(period))
        if payload is not None:
            sanitized[period] = payload
    return sanitized


def _normalize_day_entry(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    last_notified = _coerce_last_notified(value.get("last_notified_at"))
    return {
        "met": bool(value.get("met")),
        "stage": _coerce_stage(value.get("stage")),
        "last_notified_at": last_notified,
    }


def _normalize_warning_throttle_entry(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    return {
        "last_seen_at": _coerce_iso_datetime_text(value.get("last_seen_at")),
        "last_warned_at": _coerce_iso_datetime_text(value.get("last_warned_at")),
        "consecutive_runs": _coerce_nonnegative_int(value.get("consecutive_runs")),
        "suppressed_runs": _coerce_nonnegative_int(value.get("suppressed_runs")),
        "last_category": _coerce_warning_category(value.get("last_category")),
    }


def sanitize_warning_throttle(value: Any) -> Dict[str, Dict[str, Any]]:
    sanitized = default_warning_throttle_state()
    if not isinstance(value, dict):
        return sanitized
    for key in WARNING_THROTTLE_KEYS:
        sanitized[key] = _normalize_warning_throttle_entry(value.get(key))
    return sanitized


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
        if _is_iso_date_key(raw_key):
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
        "last_fetched_at": _coerce_iso_datetime_text(raw.get("last_fetched_at")),
        "warning_throttle": sanitize_warning_throttle(raw.get("warning_throttle")),
        "days": normalized_days,
    }


def save_monitor_state(state: Dict[str, Any], *, path: Path = MONITOR_STATE_PATH) -> None:
    payload = {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "etag": state.get("etag"),
        "last_modified": state.get("last_modified"),
        "last_fetched_at": _coerce_iso_datetime_text(state.get("last_fetched_at")),
        "warning_throttle": sanitize_warning_throttle(state.get("warning_throttle")),
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
    return {
        "generated_at": raw.get("generated_at"),
        "mask_level": _coerce_nonnegative_int(raw.get("mask_level", default_mask_level)),
        "days": sanitize_masked_days(raw.get("days")),
    }


def save_masked_history(data: Dict[str, Any], path: Path = HISTORY_MASKED_PATH) -> None:
    payload = {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "mask_level": _coerce_nonnegative_int(data.get("mask_level", 1)),
        "days": sanitize_masked_days(data.get("days", {})),
    }
    _write_json_atomic(path, payload, sort_keys=True)


def load_summary_store(path: Path = SUMMARY_MASKED_PATH) -> Dict[str, Any]:
    return sanitize_summary_store(_read_json_file(path, default={}))


def save_summary_store(path: Path, data: Dict[str, Any]) -> None:
    _write_json_atomic(path, sanitize_summary_store(data), sort_keys=True)


__all__ = [
    "HISTORY_MASKED_PATH",
    "JST",
    "LEGACY_STATE_PATH",
    "MONITOR_STATE_PATH",
    "SUMMARY_MASKED_PATH",
    "default_monitor_state",
    "default_warning_throttle_state",
    "load_masked_history",
    "load_monitor_state",
    "load_summary_store",
    "migrate_legacy_state",
    "save_masked_history",
    "save_monitor_state",
    "save_summary_store",
    "sanitize_masked_days",
    "sanitize_summary_store",
    "sanitize_warning_throttle",
]
