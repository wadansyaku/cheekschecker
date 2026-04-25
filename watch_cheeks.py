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
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from playwright.async_api import async_playwright

from src.calendar_parser import (
    business_dow_label as _business_dow_label,
    infer_entry_date,
    parse_day_entries as _parse_day_entries,
)
from src.domain import DOW_EN, DOW_JP, DailyEntry, JST
from src.logging_config import configure_logging, get_logger
from src.masking import (
    DEFAULT_MASKING_CONFIG,
    CountBand,
    MaskingConfig,
    RatioBand,
    load_masking_config,
)
from src.notifications import (
    append_step_summary as _append_step_summary,
    send_slack_message as _send_slack_message,
)
from src.notification_state import evaluate_stage_transition
from src import public_state
from src import public_summary

# Initialize structured logging
configure_logging(debug=bool(int(os.getenv("DEBUG_LOG", "0"))))
LOGGER = get_logger(__name__)

MONITOR_STATE_PATH = public_state.MONITOR_STATE_PATH
LEGACY_STATE_PATH = public_state.LEGACY_STATE_PATH
HISTORY_MASKED_PATH = public_state.HISTORY_MASKED_PATH

STEP_SUMMARY_TITLE_MONITOR = "Cheeks Monitor"
STEP_SUMMARY_TITLES = {
    7: "Cheeks Weekly Summary",
    30: "Cheeks Monthly Summary",
}

DEFAULT_ROLLOVER_HOURS = {"Sun": 2, "Mon": 0, "Tue": 5, "Wed": 5, "Thu": 5, "Fri": 6, "Sat": 6}
DEFAULT_COOLDOWN_MINUTES = 180
DEFAULT_BONUS_SINGLE_DELTA = 2
DEFAULT_BONUS_RATIO_THRESHOLD = 0.50
DEFAULT_NOTIFY_FROM_TODAY = 1
DEFAULT_MASK_LEVEL = 1
DEFAULT_HEAD_SKIP_MAX_AGE_MINUTES = 180
DEFAULT_WARNING_THROTTLE_MINUTES = 180
MONITOR_FETCH_FAILURE_WARNING_KEY = "monitor_fetch_failure"
FETCH_UNAVAILABLE_WARNING_CATEGORY = "fetch_unavailable"

DEFAULT_TARGET_URL = "http://cheeks.nagoya/yoyaku.shtml"
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_USER_AGENT_ID = "CheekscheckerBot/1.0"

MASK_COUNT_BANDS: list[CountBand] = list(DEFAULT_MASKING_CONFIG.count_bands)
MASK_TOTAL_BANDS: list[CountBand] = list(DEFAULT_MASKING_CONFIG.total_bands)
MASK_RATIO_BANDS: list[RatioBand] = list(DEFAULT_MASKING_CONFIG.ratio_bands)
MASK_LEVEL2_WORDS = {
    key: list(words) for key, words in DEFAULT_MASKING_CONFIG.level2_words.items()
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
    allow_fetch_failure: bool
    head_skip_max_age_minutes: int
    warning_throttle_minutes: int = DEFAULT_WARNING_THROTTLE_MINUTES
    masking_config: MaskingConfig = field(default=DEFAULT_MASKING_CONFIG)


class CalendarFetchError(RuntimeError):
    """Raised when both primary and fallback fetch paths fail."""


@dataclass
class SummaryBundle:
    period_label: str
    period_days: List[DailyEntry]
    previous_days: List[DailyEntry]




def append_step_summary(title: str, sections: Sequence[Tuple[str, Sequence[str]]], fallback: str) -> None:
    _append_step_summary(
        title,
        sections,
        fallback,
        empty_fallback="該当なし",
        logger=LOGGER,
    )


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


def _settings_for_log(settings: Settings) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for item in fields(settings):
        value = getattr(settings, item.name)
        if item.name == "slack_webhook_url":
            payload[item.name] = "<set>" if value else None
        elif item.name == "masking_config":
            payload[item.name] = "<custom>" if value != DEFAULT_MASKING_CONFIG else "<default>"
        else:
            payload[item.name] = value
    return payload


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
    if notify_mode == "changed":
        LOGGER.warning(
            "NOTIFY_MODE=changed is not guaranteed in public-safe persisted state; scheduled workflows should use newly"
        )
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
    allow_fetch_failure = os.getenv("ALLOW_FETCH_FAILURE", "0") in {"1", "true", "True"}
    head_skip_max_age_minutes = _parse_int(
        os.getenv("HEAD_SKIP_MAX_AGE_MINUTES"),
        DEFAULT_HEAD_SKIP_MAX_AGE_MINUTES,
    )
    warning_throttle_minutes = _parse_int(
        os.getenv("WARNING_THROTTLE_MINUTES"),
        DEFAULT_WARNING_THROTTLE_MINUTES,
    )
    mask_config_path = os.getenv("MASK_CONFIG_PATH")
    masking_config = load_masking_config(mask_config_path)

    settings = Settings(
        target_url=target_url,
        slack_webhook_url=slack_webhook,
        female_min=female_min,
        female_ratio_min=female_ratio_min,
        min_total=min_total,
        exclude_keywords=exclude_keywords,
        include_dow=include_dow,
        notify_mode=notify_mode,
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
        allow_fetch_failure=allow_fetch_failure,
        head_skip_max_age_minutes=head_skip_max_age_minutes,
        warning_throttle_minutes=warning_throttle_minutes,
        masking_config=masking_config,
    )
    LOGGER.debug("Loaded settings: %s", _settings_for_log(settings))
    return settings


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


def parse_day_entries(
    html: str,
    *,
    settings: Optional[Settings] = None,
    reference_date: Optional[date] = None,
) -> List[DailyEntry]:
    cfg = settings or load_settings()
    return _parse_day_entries(html, settings=cfg, reference_date=reference_date)


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


def notify_slack(payload: Dict[str, Any], settings: Settings, *, strict: bool = False) -> None:
    fallback_text = str(payload.get("text") or "Cheekschecker notification")
    _send_slack_message(
        settings.slack_webhook_url,
        payload,
        fallback_text,
        logger=LOGGER,
        retry_fallback=False,
        raise_on_failure=strict,
    )


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


def mask_entry(
    entry: DailyEntry,
    mask_level: int,
    masking_config: MaskingConfig = DEFAULT_MASKING_CONFIG,
) -> Dict[str, str]:
    if mask_level <= 1:
        return {
            "single": _bin_value(entry.single_female, masking_config.count_bands),
            "female": _bin_value(entry.female, masking_config.count_bands),
            "total": _bin_value(entry.total, masking_config.total_bands),
            "ratio": _bin_ratio(entry.ratio, masking_config.ratio_bands),
        }

    level2_words = masking_config.level2_words
    level2_divisors = masking_config.level2_divisors

    single_words = level2_words.get("single", ("静", "穏", "賑"))
    female_words = level2_words.get("female", ("薄", "適", "厚"))
    ratio_words = level2_words.get("ratio", ("低", "中", "高"))
    total_words = level2_words.get("total", ("少", "並", "盛"))

    single_divisor = max(1, level2_divisors.get("single", 3))
    female_divisor = max(1, level2_divisors.get("female", 4))
    total_divisor = max(1, level2_divisors.get("total", 15))

    single_index = min(len(single_words) - 1, entry.single_female // single_divisor)
    female_index = min(len(female_words) - 1, entry.female // female_divisor)
    total_index = min(len(total_words) - 1, entry.total // total_divisor)

    thresholds = masking_config.level2_ratio_thresholds
    ratio_index = 0
    while ratio_index < len(thresholds) and entry.ratio >= thresholds[ratio_index]:
        ratio_index += 1
    ratio_index = min(len(ratio_words) - 1, ratio_index)

    return {
        "single": single_words[single_index],
        "female": female_words[female_index],
        "ratio": ratio_words[ratio_index],
        "total": total_words[total_index],
    }


def load_state(reference_date: date) -> Dict[str, Any]:
    return public_state.load_monitor_state(
        reference_date=reference_date,
        legacy_day_resolver=infer_entry_date,
        path=MONITOR_STATE_PATH,
        legacy_path=LEGACY_STATE_PATH,
    )


def upgrade_state_keys(state: Dict[str, Any], reference_date: date) -> Dict[str, Any]:
    return public_state.migrate_legacy_state(
        state,
        reference_date=reference_date,
        legacy_day_resolver=infer_entry_date,
    )


def save_state(state: Dict[str, Any]) -> None:
    public_state.save_monitor_state(state, path=MONITOR_STATE_PATH)
    LOGGER.debug("Monitor state saved to %s", MONITOR_STATE_PATH)


def load_masked_history() -> Dict[str, Any]:
    return public_state.load_masked_history(
        HISTORY_MASKED_PATH,
        default_mask_level=DEFAULT_MASK_LEVEL,
    )


def save_masked_history(data: Dict[str, Any]) -> None:
    public_state.save_masked_history(data, HISTORY_MASKED_PATH)
    LOGGER.debug("history_masked.json updated")


def _robots_agent_matches(agent: str, user_agent: str) -> bool:
    normalized_agent = agent.strip().lower()
    normalized_user_agent = user_agent.strip().lower()
    product_token = normalized_user_agent.split("/", 1)[0]
    return (
        normalized_agent == "*"
        or normalized_agent == normalized_user_agent
        or normalized_agent == product_token
    )


def _parse_matching_robots_rules(
    robots_text: str,
    *,
    user_agent: str,
) -> List[Tuple[str, str]]:
    groups: List[Tuple[Tuple[str, ...], List[Tuple[str, str]]]] = []
    current_agents: List[str] = []
    current_rules: List[Tuple[str, str]] = []
    seen_rule = False

    def flush_group() -> None:
        if current_agents:
            groups.append((tuple(current_agents), list(current_rules)))

    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if seen_rule:
                flush_group()
                current_agents = []
                current_rules = []
                seen_rule = False
            current_agents.append(value)
            continue
        if field in {"allow", "disallow"} and current_agents:
            current_rules.append((field, value))
            seen_rule = True

    flush_group()

    matching: List[Tuple[int, List[Tuple[str, str]]]] = []
    for agents, group_rules in groups:
        matching_agents = [
            agent for agent in agents if _robots_agent_matches(agent, user_agent)
        ]
        if not matching_agents:
            continue
        specificity = max(0 if agent == "*" else len(agent) for agent in matching_agents)
        matching.append((specificity, group_rules))

    if not matching:
        return []

    best_specificity = max(specificity for specificity, _ in matching)
    selected_rules: List[Tuple[str, str]] = []
    for specificity, group_rules in matching:
        if specificity == best_specificity:
            selected_rules.extend(group_rules)
    return selected_rules


def _robots_path_allowed(
    robots_text: str,
    *,
    target_url: str,
    user_agent: str = DEFAULT_USER_AGENT_ID,
) -> bool:
    parsed = urlparse(target_url)
    target_path = parsed.path or "/"
    rules = _parse_matching_robots_rules(robots_text, user_agent=user_agent)
    best_match: Optional[Tuple[int, str]] = None

    for directive, rule_path in rules:
        if directive == "disallow" and not rule_path:
            continue
        if directive == "allow" and not rule_path:
            continue
        if not target_path.startswith(rule_path):
            continue
        candidate = (len(rule_path), directive)
        if best_match is None:
            best_match = candidate
            continue
        if candidate[0] > best_match[0]:
            best_match = candidate
            continue
        if candidate[0] == best_match[0] and candidate[1] == "allow":
            best_match = candidate

    if best_match is None:
        return True
    return best_match[1] == "allow"


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

        allowed = _robots_path_allowed(response.text, target_url=settings.target_url)
        if not allowed:
            LOGGER.warning("robots.txt disallows target_url=%s; skipping fetch", settings.target_url)
        return allowed
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("robots enforcement failed (%s); proceeding cautiously", exc)
        return True


async def fetch_calendar_html(settings: Settings) -> Tuple[str, str]:
    LOGGER.info("Fetching calendar HTML from %s", settings.target_url)
    try:
        return await _fetch_calendar_html_playwright(settings)
    except Exception as exc:  # pragma: no cover - fallback path is difficult to trigger in unit tests without Playwright
        LOGGER.warning("Playwright fetch failed (%s); falling back to requests", exc)
        try:
            return _fetch_calendar_html_requests(settings)
        except Exception as fallback_exc:
            raise CalendarFetchError(
                f"calendar fetch failed via playwright and requests: {fallback_exc}"
            ) from fallback_exc


def _build_user_agent(settings: Settings) -> str:
    user_agent = DEFAULT_UA
    if settings.ua_contact:
        user_agent = f"{user_agent} {DEFAULT_USER_AGENT_ID} (+{settings.ua_contact})"
    return user_agent


async def _fetch_calendar_html_playwright(settings: Settings) -> Tuple[str, str]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        headers = {"Accept-Language": "ja,en-US;q=0.9,en;q=0.8"}
        context = await browser.new_context(
            locale="ja-JP",
            user_agent=_build_user_agent(settings),
            extra_http_headers=headers,
        )
        try:
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
                LOGGER.info(
                    "Fetched table outerHTML from frame=%s sha256=%s length=%d",
                    source_url,
                    digest,
                    len(table_html),
                )
                return table_html, source_url

            html = await page.content()
            digest = hashlib.sha256(html.encode("utf-8", "ignore")).hexdigest()
            LOGGER.info("Fetched full page content sha256=%s length=%d", digest, len(html))
            return html, source_url
        finally:
            await context.close()
            await browser.close()


def _fetch_calendar_html_requests(settings: Settings) -> Tuple[str, str]:
    headers = {
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "User-Agent": _build_user_agent(settings),
    }
    response = requests.get(settings.target_url, headers=headers, timeout=30)
    response.raise_for_status()
    digest = hashlib.sha256(response.text.encode("utf-8", "ignore")).hexdigest()
    LOGGER.info(
        "Fetched page via requests sha256=%s length=%d status=%s",
        digest,
        len(response.text),
        response.status_code,
    )
    return response.text, settings.target_url


def _short_error_message(exc: Exception, *, limit: int = 220) -> str:
    message = " ".join(str(exc).split()).strip() or exc.__class__.__name__
    if len(message) <= limit:
        return message
    return message[: limit - 3] + "..."


def _build_fetch_failure_payload(
    *,
    title: str,
    message: str,
    detail: str,
    target_url: str,
) -> Tuple[Dict[str, Any], str, List[Tuple[str, List[str]]]]:
    fallback = f"{title}: {message}"
    sections = [
        ("実行結果", [message, f"detail: {detail}"]),
    ]
    payload = {
        "text": fallback,
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title, "emoji": False},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*実行結果*\n{message}\n`{detail}`"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "カレンダーを開く"},
                        "url": target_url,
                    }
                ],
            },
        ],
    }
    return payload, fallback, sections


def _build_summary_fetch_failure_raw_payload(
    *,
    days: int,
    logical_today: date,
    error_message: str,
) -> Dict[str, Any]:
    label = f"過去{days}日" if days != 30 else "過去30日"
    return {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "period_label": label,
        "window_days": days,
        "logical_today": logical_today.isoformat(),
        "days": [],
        "previous_days": [],
        "fetch_status": "unavailable",
        "fetch_error": error_message,
    }


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
    max_notify_day = logical_today + timedelta(days=1)
    for entry in entries:
        if entry.business_day < cutoff:
            continue
        if notify_from_today and entry.business_day < logical_today:
            continue
        if entry.business_day > max_notify_day:
            continue
        filtered.append(entry)
    return filtered


def _date_key(target: date) -> str:
    return target.isoformat()


def _merge_state_counts(state_entry: Dict[str, Any], entry: DailyEntry) -> None:
    state_entry["met"] = bool(entry.meets)


def _build_notification_sections(
    stage_notifications: List[Tuple[DailyEntry, str]],
    newly_met: List[DailyEntry],
    changed_counts: List[DailyEntry],
    logical_today: date,
) -> List[Tuple[str, List[str]]]:
    """Build notification sections for step summary.

    Args:
        stage_notifications: Entries with stage transitions
        newly_met: Newly qualifying entries
        changed_counts: Entries with count changes
        logical_today: Logical business day

    Returns:
        List of (section_title, section_lines) tuples
    """
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

    return sections


def _process_single_entry(
    entry: DailyEntry,
    prev_state: Dict[str, Any],
    now_ts: int,
    settings: Settings,
) -> Tuple[Optional[str], str, int, Dict[str, Any]]:
    """Process a single entry for notifications.

    Args:
        entry: Entry to process
        prev_state: Previous state for this entry
        now_ts: Current timestamp
        settings: Application settings

    Returns:
        Tuple of (action, stage, last_notified, new_state_entry)
    """
    action, stage, last_notified = evaluate_stage_transition(
        entry,
        prev_state,
        now_ts=now_ts,
        cooldown_seconds=settings.cooldown_minutes * 60,
        bonus_single_delta=settings.bonus_single_delta,
        bonus_ratio_threshold=settings.bonus_ratio_threshold,
    )

    state_entry = dict(prev_state)
    _merge_state_counts(state_entry, entry)
    state_entry["stage"] = stage
    state_entry["last_notified_at"] = last_notified

    return action, stage, last_notified, state_entry


def _categorize_notifications(
    entry: DailyEntry,
    action: Optional[str],
    prev_state: Dict[str, Any],
    settings: Settings,
    stage_notifications: List[Tuple[DailyEntry, str]],
    newly_met: List[DailyEntry],
    changed_counts: List[DailyEntry],
) -> None:
    """Categorize entry into appropriate notification lists.

    Args:
        entry: Entry to categorize
        action: Stage transition action (if any)
        prev_state: Previous state
        settings: Application settings
        stage_notifications: List to append stage notifications
        newly_met: List to append newly met entries
        changed_counts: List to append count-changed entries
    """
    if action:
        stage_notifications.append((entry, action))

    previous_met = bool(prev_state.get("met"))
    if entry.meets and not previous_met:
        newly_met.append(entry)

    if entry.meets and settings.notify_mode == "changed":
        counts_before = prev_state.get("counts")
        if counts_before:
            if (
                counts_before.get("female") != entry.female
                or counts_before.get("single_female") != entry.single_female
                or counts_before.get("total") != entry.total
            ):
                changed_counts.append(entry)
        else:
            LOGGER.debug(
                "Skipping changed notification for %s because public-safe persisted state does not store prior counts",
                entry.business_day,
            )


def process_notifications(
    entries: Sequence[DailyEntry],
    *,
    settings: Settings,
    logical_today: date,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Process entries and send notifications based on stage transitions.

    Args:
        entries: Entries to process
        settings: Application settings
        logical_today: Logical business day
        state: Current state (defaults to loading from file)

    Returns:
        Updated state dictionary
    """
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

        action, stage, last_notified, state_entry = _process_single_entry(
            entry, prev_state, now_ts, settings
        )
        days_state[key] = state_entry

        _categorize_notifications(
            entry,
            action,
            prev_state,
            settings,
            stage_notifications,
            newly_met,
            changed_counts,
        )

    save_state(state)

    sections = _build_notification_sections(
        stage_notifications, newly_met, changed_counts, logical_today
    )
    fallback = build_fallback_text(
        stage_notifications, newly_met, changed_counts, logical_today
    )
    append_step_summary(STEP_SUMMARY_TITLE_MONITOR, sections, fallback)

    if not (stage_notifications or newly_met or changed_counts):
        LOGGER.info("No notifications to send for logical_today=%s", logical_today)
        return state

    blocks = _build_slack_blocks(
        stage_notifications, newly_met, changed_counts, logical_today, settings
    )
    payload = {"text": fallback, "blocks": blocks or None}
    if settings.ping_channel and not blocks:
        payload["text"] = f"<!channel> {payload['text']}"
    notify_slack(payload, settings)

    return state


def _build_monitor_diagnostic_entry(logical_today: date) -> DailyEntry:
    return DailyEntry(
        raw_date=logical_today,
        business_day=logical_today,
        day_of_month=logical_today.day,
        dow_en=_business_dow_label(logical_today),
        male=2,
        female=6,
        single_female=5,
        total=8,
        ratio=0.75,
        considered=True,
        meets=True,
        required_single=3,
    )


def send_monitor_slack_diagnostic(
    settings: Settings,
    *,
    logical_today: Optional[date] = None,
) -> None:
    """Send a synthetic public-safe monitor notification without mutating state."""
    if logical_today is None:
        logical_today = derive_business_day(datetime.now(tz=JST), settings.rollover_hours)

    entry = _build_monitor_diagnostic_entry(logical_today)
    stage_notifications = [(entry, "initial")]
    newly_met = [entry]
    changed_counts: list[DailyEntry] = []

    diagnostic_lines = [
        "monitor Slack 通知分岐の疎通診断です",
        "実予約データではない synthetic payload です",
        "monitor_state.json と history_masked.json は更新しません",
    ]
    sections = [("診断", diagnostic_lines)]
    sections.extend(
        _build_notification_sections(
            stage_notifications,
            newly_met,
            changed_counts,
            logical_today,
        )
    )
    fallback = "【診断】monitor Slack 通知分岐の疎通確認\n" + build_fallback_text(
        stage_notifications,
        newly_met,
        changed_counts,
        logical_today,
    )
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Cheekschecker Monitor Diagnostic*\n実予約データではない synthetic payload です。",
            },
        }
    ]
    blocks.extend(
        _build_slack_blocks(
            stage_notifications,
            newly_met,
            changed_counts,
            logical_today,
            settings,
        )
    )
    payload = {"text": fallback, "blocks": blocks}

    append_step_summary(STEP_SUMMARY_TITLE_MONITOR, sections, fallback)
    notify_slack(payload, settings, strict=True)
    LOGGER.info("Monitor Slack diagnostic notification sent")


def select_summary_bundle(entries: Sequence[DailyEntry], *, logical_today: date, days: int) -> SummaryBundle:
    start = logical_today - timedelta(days=days - 1)
    period_days = [entry for entry in entries if start <= entry.business_day <= logical_today]
    prev_start = start - timedelta(days=days)
    prev_end = start - timedelta(days=1)
    previous_days = [entry for entry in entries if prev_start <= entry.business_day <= prev_end]
    label = f"過去{days}日" if days != 30 else "過去30日"
    return SummaryBundle(label, period_days, previous_days)


def _build_raw_summary_payload(
    bundle: SummaryBundle,
    *,
    logical_today: date,
    days: int,
) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "period_label": bundle.period_label,
        "window_days": days,
        "logical_today": logical_today.isoformat(),
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


def update_masked_history(entries: Sequence[DailyEntry], *, settings: Settings) -> None:
    history = load_masked_history()
    history.setdefault("days", {})
    history["mask_level"] = settings.mask_level
    history["generated_at"] = datetime.now(tz=JST).isoformat()

    for entry in entries:
        key = _date_key(entry.business_day)
        history["days"][key] = mask_entry(
            entry, settings.mask_level, settings.masking_config
        )

    save_masked_history(history)


def _parse_state_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def _warning_throttle_entry(state: Dict[str, Any], key: str) -> Dict[str, Any]:
    throttle = public_state.sanitize_warning_throttle(state.get("warning_throttle"))
    state["warning_throttle"] = throttle
    return throttle[key]


def _reset_warning_throttle(state: Dict[str, Any], key: str) -> None:
    throttle = public_state.sanitize_warning_throttle(state.get("warning_throttle"))
    throttle[key] = public_state.default_warning_throttle_state()[key]
    state["warning_throttle"] = throttle


def _record_warning_event(
    state: Dict[str, Any],
    *,
    key: str,
    category: str,
    now: datetime,
    throttle_minutes: int,
) -> Tuple[bool, int]:
    entry = _warning_throttle_entry(state, key)
    suppressed_before = int(entry.get("suppressed_runs") or 0)
    consecutive_runs = int(entry.get("consecutive_runs") or 0) + 1
    last_warned_at = _parse_state_datetime(entry.get("last_warned_at"))
    should_warn = (
        throttle_minutes <= 0
        or last_warned_at is None
        or now.astimezone(JST) - last_warned_at >= timedelta(minutes=throttle_minutes)
    )

    entry["last_seen_at"] = now.astimezone(JST).isoformat()
    entry["consecutive_runs"] = consecutive_runs
    entry["last_category"] = category
    if should_warn:
        entry["last_warned_at"] = now.astimezone(JST).isoformat()
        entry["suppressed_runs"] = 0
        return True, suppressed_before

    entry["suppressed_runs"] = suppressed_before + 1
    return False, entry["suppressed_runs"]


def _can_skip_for_cached_headers(
    prev_state: Dict[str, Any],
    *,
    now: datetime,
    max_age_minutes: int,
) -> bool:
    if max_age_minutes <= 0:
        return False
    last_fetched_at = _parse_state_datetime(prev_state.get("last_fetched_at"))
    if last_fetched_at is None:
        return False
    age = now.astimezone(JST) - last_fetched_at
    if age < timedelta(0):
        return False
    return age <= timedelta(minutes=max_age_minutes)


def should_skip_by_http_headers(
    settings: Settings,
    prev_state: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Tuple[bool, Dict[str, Optional[str]]]:
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
        can_skip = same and _can_skip_for_cached_headers(
            prev_state,
            now=now or datetime.now(tz=JST),
            max_age_minutes=settings.head_skip_max_age_minutes,
        )
        if same and not can_skip:
            LOGGER.info(
                "HEAD matched cached validators but fetch is forced because last successful fetch is stale or unknown"
            )
        LOGGER.info("HEAD check completed. skip=%s", can_skip)
        return can_skip, {"etag": etag, "last_modified": last_modified}
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

    try:
        html, _ = asyncio.run(fetch_calendar_html(settings))
    except CalendarFetchError as exc:
        if not settings.allow_fetch_failure:
            raise
        detail = _short_error_message(exc)
        message = "外部サイト取得失敗のため今回の monitor はスキップしました"
        payload, fallback, sections = _build_fetch_failure_payload(
            title="Cheekschecker Monitor",
            message=message,
            detail=detail,
            target_url=settings.target_url,
        )
        LOGGER.warning("%s detail=%s", message, detail)
        should_warn, suppressed_runs = _record_warning_event(
            state,
            key=MONITOR_FETCH_FAILURE_WARNING_KEY,
            category=FETCH_UNAVAILABLE_WARNING_CATEGORY,
            now=now,
            throttle_minutes=settings.warning_throttle_minutes,
        )
        save_state(state)
        if should_warn:
            if suppressed_runs:
                sections.append(("警告抑制", [f"前回通知後に {suppressed_runs} 回の Slack 警告を抑制"]))
            append_step_summary(STEP_SUMMARY_TITLE_MONITOR, sections, fallback)
            notify_slack(payload, settings)
        else:
            sections.append(
                (
                    "警告抑制",
                    [
                        f"Slack warning suppressed by WARNING_THROTTLE_MINUTES={settings.warning_throttle_minutes}",
                        f"suppressed_runs={suppressed_runs}",
                    ],
                )
            )
            append_step_summary(
                STEP_SUMMARY_TITLE_MONITOR,
                sections,
                f"{fallback} (Slack warning suppressed)",
            )
        return
    state["last_fetched_at"] = datetime.now(tz=JST).isoformat()
    _reset_warning_throttle(state, MONITOR_FETCH_FAILURE_WARNING_KEY)
    if output_sanitized:
        output_sanitized.parent.mkdir(parents=True, exist_ok=True)
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
    try:
        html, _ = asyncio.run(fetch_calendar_html(settings))
    except CalendarFetchError as exc:
        if not settings.allow_fetch_failure:
            raise
        detail = _short_error_message(exc)
        if raw_output:
            raw_output.parent.mkdir(parents=True, exist_ok=True)
            raw_output.write_text(
                json.dumps(
                    _build_summary_fetch_failure_raw_payload(
                        days=days,
                        logical_today=logical_today,
                        error_message=detail,
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            LOGGER.warning(
                "Summary source unavailable; wrote failure marker to %s detail=%s",
                raw_output,
                detail,
            )
        else:
            LOGGER.warning("Summary source unavailable detail=%s", detail)
        if notify:
            summary_title = STEP_SUMMARY_TITLES.get(days, f"Cheeks Summary {days}")
            header_title = "週次サマリー" if days == 7 else "月次サマリー"
            payload, fallback_text, sections = _build_fetch_failure_payload(
                title=f"Cheekschecker {header_title}",
                message="外部サイト取得失敗のため今回の summary はスキップしました",
                detail=detail,
                target_url=settings.target_url,
            )
            append_step_summary(summary_title, sections, fallback_text)
            notify_slack(payload, settings)
        return
    entries = parse_day_entries(html, settings=settings, reference_date=logical_today)
    update_masked_history(entries, settings=settings)
    bundle = select_summary_bundle(entries, logical_today=logical_today, days=days)
    raw_payload = _build_raw_summary_payload(bundle, logical_today=logical_today, days=days)

    if raw_output:
        raw_output.parent.mkdir(parents=True, exist_ok=True)
        raw_output.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Summary raw dataset written to %s", raw_output)

    if not notify:
        LOGGER.info("Slack notification suppressed by flag")
        return

    dataset = public_summary.raw_dataset_from_dict(raw_payload)
    history_meta = load_masked_history()
    period_key = "weekly" if days == 7 else "monthly"
    context = public_summary.build_summary_context(
        period_key,
        dataset,
        history_meta,
        masking_config=settings.masking_config,
    )
    summary_title = STEP_SUMMARY_TITLES.get(days, f"Cheeks Summary {days}")
    header_title = "週次サマリー" if days == 7 else "月次サマリー"

    if context is None:
        payload, fallback_text, sections = public_summary.build_placeholder_summary_payload(
            f"Cheekschecker {header_title}",
            "No data for this period / 集計対象なし",
        )
    else:
        payload, fallback_text, sections = public_summary.build_slack_payload(
            context,
            header_title,
            logical_today=logical_today,
        )

    append_step_summary(summary_title, sections, fallback_text)
    notify_slack(payload, settings)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cheeks calendar monitor")
    subparsers = parser.add_subparsers(dest="command", required=False)

    monitor_parser = subparsers.add_parser("monitor", help="Run monitoring notifications")
    monitor_parser.add_argument("--sanitized-output", type=Path, help="Path to save sanitized HTML")

    subparsers.add_parser(
        "monitor-diagnostic",
        help="Send a synthetic public-safe monitor Slack diagnostic",
    )

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
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    settings = load_settings()

    if args.command in {None, "monitor"}:
        sanitized_path = getattr(args, "sanitized_output", None)
        monitor(settings, output_sanitized=sanitized_path)
    elif args.command == "monitor-diagnostic":
        send_monitor_slack_diagnostic(settings)
    elif args.command == "summary":
        raw_output = getattr(args, "raw_output", None)
        summary(settings, days=args.days, raw_output=raw_output, notify=not args.no_notify)
    else:  # pragma: no cover - argparse guards
        parser.print_help()


if __name__ == "__main__":
    main()
