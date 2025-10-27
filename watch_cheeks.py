#!/usr/bin/env python3
"""Cheeks calendar monitor with privacy-aware summaries and Slack notifications.

This module provides two entry points:

* ``monitor`` (default) fetches the reservation calendar, detects qualifying days
  for the current and future business days, and sends Slack notifications while
  persisting state for stage transitions.
* ``summary`` aggregates trailing statistics (weekly/monthly) and posts masked
  history information plus trend analysis to Slack.

Public operation requirements are addressed via masking levels, robots.txt
compliance, and privacy-conscious artifact generation.
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from zoneinfo import ZoneInfo

from src.logging_config import configure_logging, get_logger

# Initialize structured logging
configure_logging(debug=bool(int(os.getenv("DEBUG_LOG", "0"))))
LOGGER = get_logger(__name__)

STATE_PATH = Path("state.json")
HISTORY_MASKED_PATH = Path("history_masked.json")
JST = ZoneInfo("Asia/Tokyo")

STEP_SUMMARY_TITLE_MONITOR = "Cheeks Monitor"

DOW_EN = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
DOW_JP = {"Sun": "日", "Mon": "月", "Tue": "火", "Wed": "水", "Thu": "木", "Fri": "金", "Sat": "土"}

DEFAULT_ROLLOVER_HOURS = {"Sun": 2, "Mon": 0, "Tue": 5, "Wed": 5, "Thu": 5, "Fri": 6, "Sat": 6}
DEFAULT_COOLDOWN_MINUTES = 180
DEFAULT_BONUS_SINGLE_DELTA = 2
DEFAULT_BONUS_RATIO_THRESHOLD = 0.50
DEFAULT_NOTIFY_FROM_TODAY = 1
DEFAULT_MASK_LEVEL = 1

DEFAULT_TARGET_URL = "http://cheeks.nagoya/yoyaku.shtml"
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_USER_AGENT_ID = "CheekscheckerBot/1.0"

DATE_KEY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FULLWIDTH_TO_ASCII = str.maketrans({
    "０": "0",
    "１": "1",
    "２": "2",
    "３": "3",
    "４": "4",
    "５": "5",
    "６": "6",
    "７": "7",
    "８": "8",
    "９": "9",
})
MULTIPLIER_PATTERN = re.compile(r"(?:[×xX＊*])\s*([0-9０-９]+)")
GROUP_COUNT_PATTERN = re.compile(r"([0-9０-９]+)\s*(?:人|名|組)")

MASK_COUNT_BANDS = [
    (0, 0, "0"),
    (1, 1, "1"),
    (2, 2, "2"),
    (3, 4, "3-4"),
    (5, 6, "5-6"),
    (7, 8, "7-8"),
    (9, None, "9+")
]
MASK_TOTAL_BANDS = [
    (0, 9, "<10"),
    (10, 19, "10-19"),
    (20, 29, "20-29"),
    (30, 49, "30-49"),
    (50, None, "50+")
]
MASK_RATIO_BANDS = [
    (0.0, 0.39, "<40%"),
    (0.40, 0.49, "40±"),
    (0.50, 0.59, "50±"),
    (0.60, 0.69, "60±"),
    (0.70, 0.79, "70±"),
    (0.80, None, "80+%"),
]
MASK_LEVEL2_WORDS = {
    "single": ["静" , "穏", "賑"],
    "female": ["薄", "適", "厚"],
    "ratio": ["低", "中", "高"],
    "total": ["少", "並", "盛"],
}


@dataclass(frozen=True)
class Settings:
    target_url: str
    slack_webhook_url: Optional[str]
    female_min: int
    female_ratio_min: float
    min_total: Optional[int]
    exclude_keywords: Tuple[str, ...]
    include_dow: Tuple[str, ...]
    notify_mode: str
    debug_summary: bool
    ping_channel: bool
    cooldown_minutes: int
    bonus_single_delta: int
    bonus_ratio_threshold: float
    ignore_older_than: int
    notify_from_today: int
    rollover_hours: Dict[str, int]
    mask_level: int
    robots_enforce: bool
    ua_contact: Optional[str]
    history_masked_path: Path = field(default=HISTORY_MASKED_PATH)


@dataclass
class DailyEntry:
    raw_date: date
    business_day: date
    day_of_month: int
    dow_en: str
    male: int
    female: int
    single_female: int
    total: int
    ratio: float
    considered: bool
    meets: bool
    required_single: int


@dataclass
class SummaryBundle:
    period_label: str
    period_days: List[DailyEntry]
    previous_days: List[DailyEntry]


def configure_logging() -> None:
    level = logging.DEBUG if os.getenv("DEBUG_LOG") == "1" else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    LOGGER.debug("Logging configured at level=%s", logging.getLevelName(level))


def append_step_summary(title: str, sections: Sequence[Tuple[str, Sequence[str]]], fallback: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"## {title}\n\n")
            if sections:
                for heading, lines in sections:
                    handle.write(f"### {heading}\n\n")
                    if lines:
                        for line in lines:
                            handle.write(f"- {line}\n")
                    else:
                        handle.write("- 該当なし\n")
                    handle.write("\n")
            else:
                handle.write(f"{fallback or '該当なし'}\n\n")
    except OSError as exc:  # pragma: no cover - filesystem edge cases
        LOGGER.debug("Failed to append step summary: %s", exc)


def _parse_keywords(raw: str) -> Tuple[str, ...]:
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _normalise_dow(values: Iterable[str]) -> Tuple[str, ...]:
    mapping = {d.lower(): d for d in DOW_EN}
    normalised: List[str] = []
    for value in values:
        lowered = value.strip().lower()
        if not lowered:
            continue
        if lowered not in mapping:
            LOGGER.warning("Unknown INCLUDE_DOW value: %s", value)
            continue
        normalised.append(mapping[lowered])
    return tuple(normalised)


def _parse_int(value: Optional[str], default: int, *, floor: int = 0) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
        return max(floor, parsed)
    except (TypeError, ValueError):
        LOGGER.warning("Failed to parse integer env=%s; using default=%s", value, default)
        return default


def _parse_float(value: Optional[str], default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        LOGGER.warning("Failed to parse float env=%s; using default=%s", value, default)
        return default


def _parse_min_total(value: Optional[str]) -> Optional[int]:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except ValueError:
        LOGGER.warning("Invalid MIN_TOTAL value=%s", value)
        return None


def _parse_rollover_hours(raw: Optional[str]) -> Dict[str, int]:
    if not raw:
        return dict(DEFAULT_ROLLOVER_HOURS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        LOGGER.warning("Failed to parse ROLLOVER_HOURS_JSON=%s (%s)", raw, exc)
        return dict(DEFAULT_ROLLOVER_HOURS)
    merged = dict(DEFAULT_ROLLOVER_HOURS)
    for key, value in data.items():
        if key not in DOW_EN:
            LOGGER.warning("Unknown rollover key=%s", key)
            continue
        try:
            merged[key] = max(0, int(value))
        except (TypeError, ValueError):
            LOGGER.warning("Invalid rollover hour for %s: %s", key, value)
    return merged


def load_settings() -> Settings:
    target_url = os.getenv("TARGET_URL", DEFAULT_TARGET_URL)
    slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
    female_min = _parse_int(os.getenv("FEMALE_MIN"), 3)
    female_ratio_min = _parse_float(os.getenv("FEMALE_RATIO_MIN"), 0.3)
    min_total = _parse_min_total(os.getenv("MIN_TOTAL"))
    exclude_keywords = _parse_keywords(os.getenv("EXCLUDE_KEYWORDS", ""))
    include_dow = _normalise_dow(os.getenv("INCLUDE_DOW", "").split(","))
    notify_mode = os.getenv("NOTIFY_MODE", "newly").strip().lower()
    if notify_mode not in {"newly", "changed"}:
        LOGGER.warning("Unsupported NOTIFY_MODE=%s; falling back to newly", notify_mode)
        notify_mode = "newly"
    debug_summary = os.getenv("DEBUG_SUMMARY") == "1"
    ping_channel = os.getenv("PING_CHANNEL", "1").strip() not in {"0", "false", "False"}
    cooldown_minutes = _parse_int(os.getenv("COOLDOWN_MINUTES"), DEFAULT_COOLDOWN_MINUTES)
    bonus_single_delta = _parse_int(os.getenv("BONUS_SINGLE_DELTA"), DEFAULT_BONUS_SINGLE_DELTA)
    bonus_ratio_threshold = _parse_float(os.getenv("BONUS_RATIO_THRESHOLD"), DEFAULT_BONUS_RATIO_THRESHOLD)
    ignore_older_than = _parse_int(os.getenv("IGNORE_OLDER_THAN"), 1)
    notify_from_today = _parse_int(os.getenv("NOTIFY_FROM_TODAY"), DEFAULT_NOTIFY_FROM_TODAY)
    rollover_hours = _parse_rollover_hours(os.getenv("ROLLOVER_HOURS_JSON"))
    mask_level = _parse_int(os.getenv("MASK_LEVEL"), DEFAULT_MASK_LEVEL)
    robots_enforce = os.getenv("ROBOTS_ENFORCE", "0") in {"1", "true", "True"}
    ua_contact = os.getenv("UA_CONTACT")

    settings = Settings(
        target_url=target_url,
        slack_webhook_url=slack_webhook,
        female_min=female_min,
        female_ratio_min=female_ratio_min,
        min_total=min_total,
        exclude_keywords=exclude_keywords,
        include_dow=include_dow,
        notify_mode=notify_mode,
        debug_summary=debug_summary,
        ping_channel=ping_channel,
        cooldown_minutes=cooldown_minutes,
        bonus_single_delta=bonus_single_delta,
        bonus_ratio_threshold=bonus_ratio_threshold,
        ignore_older_than=ignore_older_than,
        notify_from_today=notify_from_today,
        rollover_hours=rollover_hours,
        mask_level=mask_level,
        robots_enforce=robots_enforce,
        ua_contact=ua_contact,
    )
    LOGGER.debug("Loaded settings: %s", settings)
    return settings


def _business_dow_label(dt: date) -> str:
    return DOW_EN[(dt.weekday() + 1) % 7]


def derive_business_day(now: datetime, rollover_hours: Dict[str, int]) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)
    dow_label = DOW_EN[(now.weekday() + 1) % 7]
    cutoff = rollover_hours.get(dow_label, 0)
    if now.hour < cutoff:
        business_day = (now - timedelta(days=1)).date()
    else:
        business_day = now.date()
    LOGGER.info("Derived business day=%s from now=%s cutoff=%s", business_day, now.isoformat(), cutoff)
    return business_day


def _should_exclude_text(text: str, keywords: Sequence[str]) -> bool:
    lowered = text.lower()
    for keyword in keywords:
        if not keyword:
            continue
        if "スタッフ" in keyword or "staff" in keyword:
            continue
        if keyword in lowered:
            return True
    return False


def _extract_numeric_counts(text: str) -> List[int]:
    counts: List[int] = []
    for match in MULTIPLIER_PATTERN.finditer(text):
        digits = match.group(1).translate(FULLWIDTH_TO_ASCII)
        try:
            counts.append(int(digits))
        except ValueError:
            continue
    for match in GROUP_COUNT_PATTERN.finditer(text):
        digits = match.group(1).translate(FULLWIDTH_TO_ASCII)
        try:
            counts.append(int(digits))
        except ValueError:
            continue
    return counts


def _count_participant_line(text: str) -> Tuple[int, int, int]:
    male_count = text.count("♂")
    female_symbols = text.count("♀")
    numbers = _extract_numeric_counts(text)

    female_count = female_symbols
    if female_symbols > 0 and male_count == 0:
        female_count = max(female_symbols, max(numbers) if numbers else female_symbols)
    numeric_value = max(numbers) if numbers else female_count
    single = 1 if female_count == 1 and male_count == 0 and numeric_value <= 1 else 0
    return male_count, female_count, single


def infer_entry_date(day: int, reference_date: date) -> date:
    if day < 1:
        day = 1
    year = reference_date.year
    month = reference_date.month

    # First, try the current month
    last_day_current = calendar.monthrange(year, month)[1]
    current_month_date = date(year, month, min(day, last_day_current))

    # Calculate days difference
    days_diff = (current_month_date - reference_date).days

    # If the date is significantly in the past (more than 15 days ago),
    # it's likely from next month
    if days_diff < -15:
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        last_day_next = calendar.monthrange(next_year, next_month)[1]
        return date(next_year, next_month, min(day, last_day_next))

    # If the date is significantly in the future (more than 20 days ahead),
    # it's likely from previous month
    if days_diff > 20:
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        last_day_prev = calendar.monthrange(prev_year, prev_month)[1]
        return date(prev_year, prev_month, min(day, last_day_prev))

    # Otherwise, return the current month date
    return current_month_date


def parse_day_entries(
    html: str,
    *,
    settings: Optional[Settings] = None,
    reference_date: Optional[date] = None,
) -> List[DailyEntry]:
    cfg = settings or load_settings()
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", attrs={"border": "2"})
    if not table:
        LOGGER.warning("Calendar table not found; returning empty list")
        return []

    today = reference_date or datetime.now(tz=JST).date()
    results: List[DailyEntry] = []

    rows = table.find_all("tr")
    if not rows:
        return []

    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue
        for cell in cells:
            parts = [part.strip() for part in cell.stripped_strings if part.strip()]
            if not parts:
                continue
            number_match = None
            for part in parts:
                number_match = re.search(r"(\d{1,2})", part)
                if number_match:
                    break
            if not number_match:
                continue
            day_of_month = int(number_match.group(1))
            cell_date = infer_entry_date(day_of_month, today)
            business_dow = _business_dow_label(cell_date)

            content_lines: List[str] = []
            for part in parts:
                if re.fullmatch(r"\d{1,2}", part):
                    continue
                if part.lower() in {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}:
                    continue
                content_lines.append(part)

            male_total = female_total = single_total = 0
            valid_lines: List[str] = []
            for line in content_lines:
                if _should_exclude_text(line, cfg.exclude_keywords):
                    continue
                male, female, single = _count_participant_line(line)
                male_total += male
                female_total += female
                single_total += single
                valid_lines.append(line)

            total = male_total + female_total
            ratio = (female_total / total) if total else 0.0
            dow_value = business_dow
            considered = True
            if cfg.include_dow and dow_value not in cfg.include_dow:
                considered = False
            if cfg.min_total is not None and total < cfg.min_total:
                considered = False

            required_single = 5 if business_dow in {"Fri", "Sat"} else 3
            female_required = max(cfg.female_min, required_single)
            ratio_threshold = max(0.40, cfg.female_ratio_min)
            meets = (
                considered
                and single_total >= required_single
                and female_total >= female_required
                and ratio >= ratio_threshold
                and total > 0
            )

            entry = DailyEntry(
                raw_date=cell_date,
                business_day=cell_date,
                day_of_month=day_of_month,
                dow_en=business_dow,
                male=male_total,
                female=female_total,
                single_female=single_total,
                total=total,
                ratio=round(ratio, 4),
                considered=considered,
                meets=meets,
                required_single=required_single,
            )
            LOGGER.debug("Parsed entry: %s", entry)
            results.append(entry)

    results.sort(key=lambda e: e.business_day)
    LOGGER.info(
        "parsing_completed",
        entry_count=len(results),
        meets_criteria_count=sum(1 for e in results if e.meets)
    )
    return results


def log_parsing_snapshot(entries: Sequence[DailyEntry], logical_today: date) -> None:
    days = [entry.business_day.day for entry in entries]
    if days:
        LOGGER.debug(
            "[DEBUG] days_coverage: count=%d min=%s max=%s", len(days), min(days), max(days)
        )
    else:
        LOGGER.debug("[DEBUG] days_coverage: count=0 min=None max=None")
    preview_first = [
        f"{entry.business_day} 単女{entry.single_female} 女{entry.female} 男{entry.male} 全{entry.total} ({int(entry.ratio*100)}%)"
        for entry in entries[:10]
    ]
    preview_last = [
        f"{entry.business_day} 単女{entry.single_female} 女{entry.female} 男{entry.male} 全{entry.total} ({int(entry.ratio*100)}%)"
        for entry in entries[-5:]
    ]
    LOGGER.debug("[DEBUG] first10: %s", preview_first)
    LOGGER.debug("[DEBUG] last5: %s", preview_last)

    latest_nonzero = None
    for entry in reversed(entries):
        if entry.total > 0:
            latest_nonzero = (
                f"{entry.business_day} {entry.dow_en} total={entry.total} female={entry.female} single={entry.single_female} ratio={entry.ratio:.2f}"
            )
            break
    LOGGER.debug("[DEBUG] latest_nonzero: %s", latest_nonzero or "なし")


def _coerce_stage(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"none", "initial", "bonus"}:
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


def evaluate_stage_transition(
    entry: DailyEntry,
    prev_state: Optional[Dict[str, Any]],
    *,
    now_ts: int,
    cooldown_seconds: int,
    bonus_single_delta: int,
    bonus_ratio_threshold: float,
) -> Tuple[Optional[str], str, Optional[int]]:
    if not entry.meets:
        return None, "none", None

    prev_stage = _coerce_stage(prev_state.get("stage")) if prev_state else "none"
    prev_last = _coerce_last_notified(prev_state.get("last_notified_at")) if prev_state else None

    stage = prev_stage
    last = prev_last
    action: Optional[str] = None

    bonus_by_single = entry.single_female >= entry.required_single + bonus_single_delta
    bonus_by_ratio = entry.ratio >= bonus_ratio_threshold

    if stage == "none":
        action = "initial"
        stage = "initial"
        last = now_ts
    elif stage == "initial":
        if bonus_by_single or bonus_by_ratio:
            action = "bonus"
            stage = "bonus"
            last = now_ts
    elif stage == "bonus":
        if last is None or now_ts - last >= cooldown_seconds:
            stage = "initial"
    else:
        stage = "initial"
        action = "initial"
        last = now_ts

    return action, stage, last


def _format_business_label(target: date, logical_today: date) -> str:
    label = f"{target.day}日({DOW_JP[_business_dow_label(target)]})"
    if target == logical_today + timedelta(days=1):
        return f"明日: {label}"
    if target > logical_today + timedelta(days=1):
        days_ahead = (target - logical_today).days
        return f"{days_ahead}日後: {label}"
    return label


def _format_stage_notification(entry: DailyEntry, notification_type: str, logical_today: date) -> str:
    percent = int(round(entry.ratio * 100))
    label_map = {"initial": "初回", "bonus": "追加"}
    prefix = label_map.get(notification_type, notification_type)
    label = _format_business_label(entry.business_day, logical_today)
    return (
        f"[{prefix}] {label}: 単女{entry.single_female} 女{entry.female} "
        f"/全{entry.total} ({percent}%)"
    )


def _format_entry(entry: DailyEntry, logical_today: date, include_male: bool = False) -> str:
    parts = [f"単女{entry.single_female}", f"女{entry.female}"]
    if include_male:
        parts.append(f"男{entry.male}")
    parts.append(f"全{entry.total}")
    percent = int(round(entry.ratio * 100))
    label = _format_business_label(entry.business_day, logical_today)
    return f"{label}: {' '.join(parts)} ({percent}%)"


def build_fallback_text(
    stage_notifications: Sequence[Tuple[DailyEntry, str]],
    newly_met: Sequence[DailyEntry],
    changed_counts: Sequence[DailyEntry],
    logical_today: date,
) -> str:
    lines: List[str] = []
    if stage_notifications:
        lines.append("【基準達成通知】")
        lines.extend(
            f"- {_format_stage_notification(entry, action, logical_today)}"
            for entry, action in stage_notifications
        )
    if newly_met:
        lines.append("【新規で条件を満たした日】")
        lines.extend(f"- {_format_entry(entry, logical_today)}" for entry in newly_met)
    if changed_counts:
        lines.append("【人数が更新された日】")
        lines.extend(
            f"- {_format_entry(entry, logical_today, include_male=True)}"
            for entry in changed_counts
        )
    return "\n".join(lines) if lines else "該当なし"


def _build_slack_blocks(
    stage_notifications: Sequence[Tuple[DailyEntry, str]],
    newly_met: Sequence[DailyEntry],
    changed_counts: Sequence[DailyEntry],
    logical_today: date,
    settings: Settings,
) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    if settings.ping_channel:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "<!channel>"}})
    if stage_notifications:
        lines = "\n".join(
            f"• {_format_stage_notification(entry, action, logical_today)}"
            for entry, action in stage_notifications
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*基準達成通知*"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})
    if newly_met:
        lines = "\n".join(
            f"• {_format_entry(entry, logical_today)}" for entry in newly_met
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*新規成立日*"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})
    if changed_counts:
        lines = "\n".join(
            f"• {_format_entry(entry, logical_today, include_male=True)}" for entry in changed_counts
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*人数更新*"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})
    if blocks:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "カレンダーを開く"},
                        "url": settings.target_url,
                    }
                ],
            }
        )
    return blocks


def notify_slack(payload: Dict[str, Any], settings: Settings) -> None:
    webhook = settings.slack_webhook_url
    if not webhook:
        LOGGER.info("SLACK_WEBHOOK_URL is not configured; skipping Slack notification")
        LOGGER.debug("Slack payload: %s", payload)
        return
    try:
        response = requests.post(webhook, json=payload, timeout=10)
        response.raise_for_status()
        LOGGER.info("Slack notification sent. status=%s", response.status_code)
    except Exception as exc:  # pragma: no cover - network variability
        LOGGER.error("Slack notification failed: %s", exc)


def _bin_value(value: int, bands: Sequence[Tuple[int, Optional[int], str]]) -> str:
    for low, high, label in bands:
        if high is None and value >= low:
            return label
        if high is not None and low <= value <= high:
            return label
    return bands[-1][2]


def _bin_ratio(value: float, bands: Sequence[Tuple[float, Optional[float], str]]) -> str:
    for low, high, label in bands:
        if high is None and value >= low:
            return label
        if high is not None and low <= value <= high:
            return label
    return bands[-1][2]


def mask_entry(entry: DailyEntry, mask_level: int) -> Dict[str, str]:
    if mask_level <= 1:
        return {
            "single": _bin_value(entry.single_female, MASK_COUNT_BANDS),
            "female": _bin_value(entry.female, MASK_COUNT_BANDS),
            "total": _bin_value(entry.total, MASK_TOTAL_BANDS),
            "ratio": _bin_ratio(entry.ratio, MASK_RATIO_BANDS),
        }
    # level 2 -> coarse labels
    single_index = min(len(MASK_LEVEL2_WORDS["single"]) - 1, entry.single_female // 3)
    female_index = min(len(MASK_LEVEL2_WORDS["female"]) - 1, entry.female // 4)
    ratio_index = min(len(MASK_LEVEL2_WORDS["ratio"]) - 1, int(entry.ratio * 5))
    total_index = min(len(MASK_LEVEL2_WORDS["total"]) - 1, entry.total // 15)
    return {
        "single": MASK_LEVEL2_WORDS["single"][single_index],
        "female": MASK_LEVEL2_WORDS["female"][female_index],
        "ratio": MASK_LEVEL2_WORDS["ratio"][ratio_index],
        "total": MASK_LEVEL2_WORDS["total"][total_index],
    }


def load_state(reference_date: date) -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOGGER.error("Failed to decode state.json: %s", exc)
            raw = {"etag": None, "last_modified": None, "days": {}}
    else:
        raw = {"etag": None, "last_modified": None, "days": {}}
    return upgrade_state_keys(raw, reference_date)


def upgrade_state_keys(state: Dict[str, Any], reference_date: date) -> Dict[str, Any]:
    days = state.get("days")
    if not isinstance(days, dict):
        state["days"] = {}
        return state
    upgraded: Dict[str, Any] = {}
    for key, value in days.items():
        if isinstance(key, str) and DATE_KEY_PATTERN.match(key):
            upgraded[key] = value
            continue
        if isinstance(key, str) and key.isdigit():
            inferred = infer_entry_date(int(key), reference_date)
            upgraded[inferred.isoformat()] = value
            continue
        LOGGER.debug("Dropping legacy state entry key=%s", key)
    state["days"] = upgraded
    return state


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)
    LOGGER.debug("State saved to %s", STATE_PATH)


def load_masked_history() -> Dict[str, Any]:
    if HISTORY_MASKED_PATH.exists():
        try:
            return json.loads(HISTORY_MASKED_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("history_masked.json is invalid; resetting")
    return {"days": {}, "mask_level": DEFAULT_MASK_LEVEL}


def save_masked_history(data: Dict[str, Any]) -> None:
    HISTORY_MASKED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.debug("history_masked.json updated")


def check_robots_allow(settings: Settings) -> bool:
    if not settings.robots_enforce:
        return True
    parsed = urlparse(settings.target_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = requests.get(robots_url, timeout=10)
        if response.status_code >= 400:
            LOGGER.warning("robots.txt unavailable (%s); skipping enforcement", response.status_code)
            return True
        disallow_paths: List[str] = []
        current_agent: Optional[str] = None
        for line in response.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("user-agent"):
                _, agent = line.split(":", 1)
                current_agent = agent.strip()
            elif line.lower().startswith("disallow") and current_agent in {"*", DEFAULT_USER_AGENT_ID}:
                _, path = line.split(":", 1)
                disallow_paths.append(path.strip())
        path = parsed.path or "/"
        for disallow in disallow_paths:
            if not disallow:
                continue
            if path.startswith(disallow):
                LOGGER.warning("robots.txt disallows path=%s; skipping fetch", path)
                return False
        return True
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("robots enforcement failed (%s); proceeding cautiously", exc)
        return True


async def fetch_calendar_html(settings: Settings) -> Tuple[str, str]:
    LOGGER.info("Fetching calendar HTML from %s", settings.target_url)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        headers = {"Accept-Language": "ja,en-US;q=0.9,en;q=0.8"}
        user_agent = DEFAULT_UA
        if settings.ua_contact:
            user_agent = f"{user_agent} {DEFAULT_USER_AGENT_ID} (+{settings.ua_contact})"
        context = await browser.new_context(locale="ja-JP", user_agent=user_agent, extra_http_headers=headers)
        page = await context.new_page()
        await page.goto(settings.target_url, wait_until="domcontentloaded", timeout=60_000)

        async def extract_from_frame(frame) -> Optional[str]:
            try:
                return await frame.evaluate(
                    "() => { const table = document.querySelector(\"table[border='2']\"); return table ? table.outerHTML : null; }"
                )
            except Exception as exc:  # pragma: no cover
                LOGGER.debug("Frame extraction failed (%s): %s", getattr(frame, "url", ""), exc)
                return None

        table_html: Optional[str] = await extract_from_frame(page.main_frame)
        source_url = page.main_frame.url or settings.target_url
        if not table_html:
            for frame in page.frames:
                if frame is page.main_frame:
                    continue
                table_html = await extract_from_frame(frame)
                if table_html:
                    source_url = frame.url or source_url
                    break
        if table_html:
            digest = hashlib.sha256(table_html.encode("utf-8", "ignore")).hexdigest()
            LOGGER.info("Fetched table outerHTML from frame=%s sha256=%s length=%d", source_url, digest, len(table_html))
            await context.close()
            await browser.close()
            return table_html, source_url

        html = await page.content()
        digest = hashlib.sha256(html.encode("utf-8", "ignore")).hexdigest()
        LOGGER.info("Fetched full page content sha256=%s length=%d", digest, len(html))
        await context.close()
        await browser.close()
        return html, source_url


def sanitize_html(html: str) -> str:
    safe_chars = set("0123456789♂♀<>/\n\r\t =\"'()-:_;,.#")
    sanitized_chars: List[str] = []
    for ch in html:
        if ch in safe_chars:
            sanitized_chars.append(ch)
        elif ch.isspace():
            sanitized_chars.append(" ")
        elif ch in {"・", "~", "〜"}:
            sanitized_chars.append(ch)
        else:
            sanitized_chars.append("□")
    return "".join(sanitized_chars)


def _filter_entries_for_notifications(
    entries: Sequence[DailyEntry],
    *,
    logical_today: date,
    notify_from_today: int,
    ignore_older_than: int,
) -> List[DailyEntry]:
    filtered: List[DailyEntry] = []
    cutoff = logical_today - timedelta(days=ignore_older_than)
    for entry in entries:
        if entry.business_day < cutoff:
            continue
        if notify_from_today and entry.business_day < logical_today:
            continue
        filtered.append(entry)
    return filtered


def _date_key(target: date) -> str:
    return target.isoformat()


def _merge_state_counts(state_entry: Dict[str, Any], entry: DailyEntry) -> None:
    state_entry["counts"] = {
        "male": entry.male,
        "female": entry.female,
        "single_female": entry.single_female,
        "total": entry.total,
        "ratio": entry.ratio,
    }
    state_entry["met"] = bool(entry.meets)


def process_notifications(
    entries: Sequence[DailyEntry],
    *,
    settings: Settings,
    logical_today: date,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now_ts = int(time.time())
    state = state if state is not None else load_state(logical_today)
    days_state = state.setdefault("days", {})

    filtered = _filter_entries_for_notifications(
        entries,
        logical_today=logical_today,
        notify_from_today=settings.notify_from_today,
        ignore_older_than=settings.ignore_older_than,
    )

    stage_notifications: List[Tuple[DailyEntry, str]] = []
    newly_met: List[DailyEntry] = []
    changed_counts: List[DailyEntry] = []

    for entry in filtered:
        key = _date_key(entry.business_day)
        prev_state = days_state.get(key, {})
        action, stage, last_notified = evaluate_stage_transition(
            entry,
            prev_state,
            now_ts=now_ts,
            cooldown_seconds=settings.cooldown_minutes * 60,
            bonus_single_delta=settings.bonus_single_delta,
            bonus_ratio_threshold=settings.bonus_ratio_threshold,
        )
        previous_met = bool(prev_state.get("met"))
        counts_before = prev_state.get("counts")

        state_entry = dict(prev_state)
        _merge_state_counts(state_entry, entry)
        state_entry["stage"] = stage
        state_entry["last_notified_at"] = last_notified
        days_state[key] = state_entry

        if action:
            stage_notifications.append((entry, action))
        if entry.meets and not previous_met:
            newly_met.append(entry)
        if entry.meets and settings.notify_mode == "changed" and counts_before:
            if (
                counts_before.get("female") != entry.female
                or counts_before.get("single_female") != entry.single_female
                or counts_before.get("total") != entry.total
            ):
                changed_counts.append(entry)

    save_state(state)

    sections: List[Tuple[str, List[str]]] = []
    if stage_notifications:
        sections.append(
            (
                "基準達成通知",
                [
                    _format_stage_notification(entry, action, logical_today)
                    for entry, action in stage_notifications
                ],
            )
        )
    if newly_met:
        sections.append(
            (
                "新規成立日",
                [_format_entry(entry, logical_today) for entry in newly_met],
            )
        )
    if changed_counts:
        sections.append(
            (
                "人数更新",
                [
                    _format_entry(entry, logical_today, include_male=True)
                    for entry in changed_counts
                ],
            )
        )

    fallback = build_fallback_text(stage_notifications, newly_met, changed_counts, logical_today)
    append_step_summary(STEP_SUMMARY_TITLE_MONITOR, sections, fallback)

    if not (stage_notifications or newly_met or changed_counts):
        LOGGER.info("No notifications to send for logical_today=%s", logical_today)
        return state

    blocks = _build_slack_blocks(stage_notifications, newly_met, changed_counts, logical_today, settings)
    payload = {"text": fallback, "blocks": blocks or None}
    if settings.ping_channel and not blocks:
        payload["text"] = f"<!channel> {payload['text']}"
    notify_slack(payload, settings)
    return state


def _summary_stats(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"avg": 0.0, "median": 0.0, "max": 0.0}
    return {
        "avg": round(sum(values) / len(values), 2),
        "median": round(statistics.median(values), 2),
        "max": round(max(values), 2),
    }


def select_summary_bundle(entries: Sequence[DailyEntry], *, logical_today: date, days: int) -> SummaryBundle:
    start = logical_today - timedelta(days=days - 1)
    period_days = [entry for entry in entries if start <= entry.business_day <= logical_today]
    prev_start = start - timedelta(days=days)
    prev_end = start - timedelta(days=1)
    previous_days = [entry for entry in entries if prev_start <= entry.business_day <= prev_end]
    label = f"過去{days}日" if days != 30 else "過去30日"
    return SummaryBundle(label, period_days, previous_days)


def _weekday_profile(entries: Sequence[DailyEntry]) -> str:
    groups: Dict[str, List[DailyEntry]] = {dow: [] for dow in DOW_EN}
    for entry in entries:
        groups[entry.dow_en].append(entry)
    summary_parts: List[str] = []
    for dow in DOW_EN:
        if not groups[dow]:
            continue
        avg_single = sum(e.single_female for e in groups[dow]) / len(groups[dow])
        avg_ratio = sum(e.ratio for e in groups[dow]) / len(groups[dow])
        summary_parts.append(
            f"{DOW_JP[dow]}: 単女{avg_single:.1f} / 比率{avg_ratio*100:.1f}%"
        )
    return "、".join(summary_parts) if summary_parts else "データ不足"


def _format_hot_day(entry: DailyEntry, logical_today: date) -> str:
    percent = int(round(entry.ratio * 100))
    label = _format_business_label(entry.business_day, logical_today)
    return f"{label} 単女{entry.single_female} 女{entry.female}/全{entry.total} ({percent}%)"


def generate_summary_payload(bundle: SummaryBundle, *, logical_today: date, settings: Settings) -> Optional[Dict[str, Any]]:
    if not bundle.period_days:
        LOGGER.info("No entries available for summary period=%s", bundle.period_label)
        return None

    singles = [entry.single_female for entry in bundle.period_days]
    females = [entry.female for entry in bundle.period_days]
    ratios = [entry.ratio * 100 for entry in bundle.period_days]

    single_stats = _summary_stats([float(x) for x in singles])
    female_stats = _summary_stats([float(x) for x in females])
    ratio_stats = _summary_stats([float(x) for x in ratios])

    prev_single_avg = sum(entry.single_female for entry in bundle.previous_days) / len(bundle.previous_days) if bundle.previous_days else 0.0
    prev_ratio_avg = (sum(entry.ratio for entry in bundle.previous_days) / len(bundle.previous_days)) * 100 if bundle.previous_days else 0.0
    delta_single = single_stats["avg"] - prev_single_avg if bundle.previous_days else 0.0
    delta_ratio = ratio_stats["avg"] - prev_ratio_avg if bundle.previous_days else 0.0

    top_days = sorted(
        bundle.period_days,
        key=lambda e: (e.ratio, e.single_female, e.female, -abs((logical_today - e.business_day).days)),
        reverse=True,
    )[:3]

    weekday_profile = _weekday_profile(bundle.period_days)

    lines = [
        f"*{bundle.period_label}サマリー*",
        f"平均 単女{single_stats['avg']:.2f} / 女{female_stats['avg']:.2f} / 比率{ratio_stats['avg']:.2f}%",
        f"中央値 単女{single_stats['median']:.2f} / 女{female_stats['median']:.2f} / 比率{ratio_stats['median']:.2f}%",
        f"最大 単女{single_stats['max']:.2f} / 女{female_stats['max']:.2f} / 比率{ratio_stats['max']:.2f}%",
        f"傾向: 単女平均 {delta_single:+.2f} / 比率平均 {delta_ratio:+.2f}pp",
        "Hot 日 Top3:",
    ]
    lines.extend(f"• {_format_hot_day(entry, logical_today)}" for entry in top_days)
    lines.append(f"曜日プロファイル: {weekday_profile}")
    lines.append(f"解析対象日数: {len(bundle.period_days)} 日")

    text = "\n".join(lines)
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": ("<!channel> " if settings.ping_channel else "") + lines[0]},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines[1:])},
        },
    ]
    if settings.ping_channel:
        text = "<!channel> " + text
    return {"text": text, "blocks": blocks}


def update_masked_history(entries: Sequence[DailyEntry], *, settings: Settings) -> None:
    history = load_masked_history()
    history.setdefault("days", {})
    history["mask_level"] = settings.mask_level
    history["generated_at"] = datetime.now(tz=JST).isoformat()

    for entry in entries:
        key = _date_key(entry.business_day)
        history["days"][key] = mask_entry(entry, settings.mask_level)

    save_masked_history(history)


def should_skip_by_http_headers(settings: Settings, prev_state: Dict[str, Any]) -> Tuple[bool, Dict[str, Optional[str]]]:
    try:
        response = requests.head(settings.target_url, timeout=10)
        response.raise_for_status()
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        same = False
        if etag and prev_state.get("etag") and etag == prev_state.get("etag"):
            same = True
        if last_modified and prev_state.get("last_modified") and last_modified == prev_state.get("last_modified"):
            same = True
        LOGGER.info("HEAD check completed. skip=%s", same)
        return same, {"etag": etag, "last_modified": last_modified}
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("HEAD check failed: %s", exc)
        return False, {"etag": None, "last_modified": None}


def monitor(settings: Settings, *, output_sanitized: Optional[Path] = None) -> None:
    if not check_robots_allow(settings):
        LOGGER.warning("Fetch skipped due to robots.txt policy")
        append_step_summary(
            STEP_SUMMARY_TITLE_MONITOR,
            [("実行結果", ["robots.txtにより取得をスキップ"])],
            "robots.txtにより取得をスキップ",
        )
        return

    now = datetime.now(tz=JST)
    logical_today = derive_business_day(now, settings.rollover_hours)
    state = load_state(logical_today)
    skip, header_info = should_skip_by_http_headers(settings, state)
    state.update(header_info)
    if skip:
        LOGGER.info("Skipping fetch because headers indicate no change")
        save_state(state)
        append_step_summary(
            STEP_SUMMARY_TITLE_MONITOR,
            [("実行結果", ["前回取得から変更なし (ETag/Last-Modified)"])],
            "前回取得から変更なし",
        )
        return

    html, _ = asyncio.run(fetch_calendar_html(settings))
    if output_sanitized:
        output_sanitized.write_text(sanitize_html(html), encoding="utf-8")
        LOGGER.info("Sanitized HTML written to %s", output_sanitized)

    entries = parse_day_entries(html, settings=settings, reference_date=logical_today)
    log_parsing_snapshot(entries, logical_today)
    process_notifications(entries, settings=settings, logical_today=logical_today, state=state)
    update_masked_history(entries, settings=settings)


def summary(
    settings: Settings,
    *,
    days: int,
    raw_output: Optional[Path] = None,
    notify: bool = True,
) -> None:
    if not check_robots_allow(settings):
        LOGGER.warning("Summary skipped due to robots.txt policy")
        return

    now = datetime.now(tz=JST)
    logical_today = derive_business_day(now, settings.rollover_hours)
    html, _ = asyncio.run(fetch_calendar_html(settings))
    entries = parse_day_entries(html, settings=settings, reference_date=logical_today)
    update_masked_history(entries, settings=settings)
    bundle = select_summary_bundle(entries, logical_today=logical_today, days=days)

    if raw_output:
        raw_payload = {
            "generated_at": datetime.now(tz=JST).isoformat(),
            "period_label": bundle.period_label,
            "days": [
                {
                    "date": entry.business_day.isoformat(),
                    "single_female": entry.single_female,
                    "female": entry.female,
                    "male": entry.male,
                    "total": entry.total,
                    "ratio": entry.ratio,
                }
                for entry in bundle.period_days
            ],
            "previous_days": [
                {
                    "date": entry.business_day.isoformat(),
                    "single_female": entry.single_female,
                    "female": entry.female,
                    "male": entry.male,
                    "total": entry.total,
                    "ratio": entry.ratio,
                }
                for entry in bundle.previous_days
            ],
        }
        raw_output.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Summary raw dataset written to %s", raw_output)

    payload = generate_summary_payload(bundle, logical_today=logical_today, settings=settings)
    if not payload:
        LOGGER.info("No summary payload generated")
        return

    if not notify:
        LOGGER.info("Slack notification suppressed by flag")
        return

    notify_slack(payload, settings)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cheeks calendar monitor")
    subparsers = parser.add_subparsers(dest="command", required=False)

    monitor_parser = subparsers.add_parser("monitor", help="Run monitoring notifications")
    monitor_parser.add_argument("--sanitized-output", type=Path, help="Path to save sanitized HTML")

    summary_parser = subparsers.add_parser("summary", help="Send weekly/monthly summary")
    summary_parser.add_argument("--days", type=int, choices=[7, 30], required=True, help="Summary window in days")
    summary_parser.add_argument("--raw-output", type=Path, help="Path to store raw summary dataset")
    summary_parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Collect data without sending Slack notifications",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    configure_logging()
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    settings = load_settings()

    if args.command in {None, "monitor"}:
        sanitized_path = getattr(args, "sanitized_output", None)
        monitor(settings, output_sanitized=sanitized_path)
    elif args.command == "summary":
        raw_output = getattr(args, "raw_output", None)
        summary(settings, days=args.days, raw_output=raw_output, notify=not args.no_notify)
    else:  # pragma: no cover - argparse guards
        parser.print_help()


if __name__ == "__main__":
    main()
