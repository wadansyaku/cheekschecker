"""Monitor the monthly calendar and notify Slack when female participation meets thresholds."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

LOGGER = logging.getLogger("cheekswatch")

STATE_PATH = Path("state.json")
DOW_EN = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


@dataclass(frozen=True)
class Settings:
    """Runtime settings derived from environment variables."""

    target_url: str
    slack_webhook_url: Optional[str]
    female_min: int
    female_ratio_min: float
    min_total: Optional[int]
    exclude_keywords: Tuple[str, ...]
    include_dow: Tuple[str, ...]
    notify_mode: str
    debug_summary: bool
    cooldown_minutes: int = 180


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

MULTIPLIER_PATTERN = re.compile(r"[×xX＊*]\s*(\d+)")
GROUP_COUNT_PATTERN = re.compile(r"([0-9０-９]+)\s*(?:人|名|組)")


def _configure_logging() -> None:
    """Initialise logging based on DEBUG_LOG flag."""
    level = logging.DEBUG if os.getenv("DEBUG_LOG") == "1" else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    LOGGER.debug("Logging configured. Level=%s", logging.getLevelName(level))


def _parse_min_total(value: Optional[str]) -> Optional[int]:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except ValueError:
        LOGGER.warning("Invalid MIN_TOTAL value %s. Ignored.", value)
        return None


def _normalise_dow(values: Iterable[str]) -> Tuple[str, ...]:
    mapping = {d.lower(): d for d in DOW_EN}
    normalised: List[str] = []
    for value in values:
        key = value.strip().lower()
        if not key:
            continue
        if key not in mapping:
            LOGGER.warning("Unknown day-of-week identifier: %s", value)
            continue
        normalised.append(mapping[key])
    return tuple(normalised)


def _parse_keywords(raw: str) -> Tuple[str, ...]:
    return tuple(keyword.strip().lower() for keyword in raw.split(",") if keyword.strip())


def load_settings() -> Settings:
    """Load runtime settings from environment variables."""

    target_url = os.getenv("TARGET_URL", "http://cheeks.nagoya/yoyaku.shtml")
    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    female_min = int(os.getenv("FEMALE_MIN", "3"))
    female_ratio_min = float(os.getenv("FEMALE_RATIO_MIN", "0.3"))
    min_total = _parse_min_total(os.getenv("MIN_TOTAL"))

    exclude_keywords = _parse_keywords(os.getenv("EXCLUDE_KEYWORDS", ""))
    include_dow = _normalise_dow(os.getenv("INCLUDE_DOW", "").split(","))

    notify_mode = os.getenv("NOTIFY_MODE", "newly").strip().lower()
    if notify_mode not in {"newly", "changed"}:
        LOGGER.warning("Unknown NOTIFY_MODE=%s. Falling back to 'newly'.", notify_mode)
        notify_mode = "newly"

    debug_summary = os.getenv("DEBUG_SUMMARY") == "1"

    cooldown_default = "180"
    cooldown_env = os.getenv("COOLDOWN_MINUTES", cooldown_default)
    try:
        cooldown_minutes = int(cooldown_env)
    except (TypeError, ValueError):
        LOGGER.warning("Invalid COOLDOWN_MINUTES=%s. Falling back to %s.", cooldown_env, cooldown_default)
        cooldown_minutes = int(cooldown_default)
    cooldown_minutes = max(0, cooldown_minutes)

    settings = Settings(
        target_url=target_url,
        slack_webhook_url=slack_webhook_url,
        female_min=female_min,
        female_ratio_min=female_ratio_min,
        min_total=min_total,
        exclude_keywords=exclude_keywords,
        include_dow=include_dow,
        notify_mode=notify_mode,
        debug_summary=debug_summary,
        cooldown_minutes=cooldown_minutes,
    )
    LOGGER.debug("Settings loaded: %s", settings)
    return settings


_configure_logging()
SETTINGS = load_settings()


def load_state() -> Dict[str, Any]:
    """Load persisted state from disk."""
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            LOGGER.debug("Loaded state: %s", state)
            return state
        except json.JSONDecodeError as exc:
            LOGGER.error("Failed to decode state.json: %s", exc)
    return {"etag": None, "last_modified": None, "days": {}}


def save_state(state: Dict[str, Any]) -> None:
    """Persist state to disk atomically."""
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)
    LOGGER.debug("State saved to %s", STATE_PATH)


def should_skip_by_http_headers(settings: Settings, prev: Dict[str, Any]) -> Tuple[bool, Dict[str, Optional[str]]]:
    """Return whether fetching can be skipped using HEAD headers."""
    try:
        response = requests.head(settings.target_url, timeout=10)
        response.raise_for_status()
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        same = False
        if etag and prev.get("etag") and etag == prev.get("etag"):
            same = True
        if last_modified and prev.get("last_modified") and last_modified == prev.get("last_modified"):
            same = True
        LOGGER.info("HEAD check completed. skip=%s", same)
        return same, {"etag": etag, "last_modified": last_modified}
    except Exception as exc:  # pragma: no cover - network exceptions vary
        LOGGER.warning("HEAD check failed: %s", exc)
        return False, {"etag": None, "last_modified": None}


async def fetch_calendar_html(settings: Settings) -> str:
    """
    対象URLを開き、メイン/サブフレームを横断して `table[border='2']` を探索。
    見つかれば outerHTML を返し、見つからなければページ全体の HTML を返す。
    """

    LOGGER.info("Fetching calendar HTML from %s", settings.target_url)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "ja,en-US;q=0.9,en;q=0.8"},
        )
        page = await context.new_page()

        table_html: Optional[str] = None
        hint = "full-page"
        try:
            await page.goto(settings.target_url, wait_until="domcontentloaded", timeout=60_000)

            async def extract_from_frame(frame) -> Optional[str]:
                try:
                    return await frame.evaluate(
                        "() => {"
                        "  const table = document.querySelector(\"table[border='2']\");"
                        "  return table ? table.outerHTML : null;"
                        "}"
                    )
                except Exception as exc:  # pragma: no cover - depends on document structure
                    LOGGER.debug("Frame extraction failed: %s", exc)
                    return None

            table_html = await extract_from_frame(page.main_frame)
            if table_html:
                hint = "table-main"
            else:
                for frame in page.frames:
                    if frame is page.main_frame:
                        continue
                    table_html = await extract_from_frame(frame)
                    if table_html:
                        hint = "table-framed"
                        break

            if table_html:
                LOGGER.debug("[DEBUG] html source hint: %s, length=%d", hint, len(table_html))
                return table_html

            html = await page.content()
            LOGGER.debug("[DEBUG] html source hint: %s, length=%d", hint, len(html))
            return html
        finally:
            await context.close()
            await browser.close()


def _should_exclude_text(text: str, keywords: Sequence[str]) -> bool:
    """Return True if the text should be excluded based on keywords."""

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
    """Extract numeric counts referenced within a participant description."""

    counts: List[int] = []
    for match in MULTIPLIER_PATTERN.finditer(text):
        try:
            counts.append(int(match.group(1)))
        except (TypeError, ValueError):
            continue
    for match in GROUP_COUNT_PATTERN.finditer(text):
        digits = match.group(1).translate(FULLWIDTH_TO_ASCII)
        try:
            counts.append(int(digits))
        except ValueError:
            continue
    return counts


def _count_participant_line(text: str) -> Tuple[int, int, int]:
    """Return the male, female and single female counts for a single line."""

    male_count = text.count("♂")
    female_symbol_count = text.count("♀")
    numbers = _extract_numeric_counts(text)
    female_count = female_symbol_count
    if female_symbol_count > 0 and male_count == 0:
        female_count = max(female_symbol_count, max(numbers) if numbers else female_symbol_count)
    numeric_value = max(numbers) if numbers else female_count
    single = 1 if female_count == 1 and male_count == 0 and numeric_value <= 1 else 0
    return male_count, female_count, single


def parse_day_entries(html: str, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Parse the calendar HTML and extract daily participation entries."""

    cfg = settings or SETTINGS
    soup = BeautifulSoup(html, "lxml")

    table = soup.select_one("table[border='2']")
    scope_hint = "table[border='2']"
    if table is None:
        table = soup.find("table")
        if table is not None:
            scope_hint = "table-fallback"
        else:
            table = soup
            scope_hint = "document"
    LOGGER.debug("Parsing day entries using scope: %s", scope_hint)

    results: List[Dict[str, Any]] = []
    rows = table.find_all("tr") if hasattr(table, "find_all") else []

    for row in rows:
        columns = [td for td in row.find_all("td") if td.get("valign", "").lower() == "top"]
        for col_index, td in enumerate(columns):
            centers = td.find_all("center")
            if not centers:
                continue

            day_text = centers[0].get_text(strip=True)
            if not day_text:
                continue
            match = re.search(r"\d+", day_text)
            if not match:
                continue
            day = int(match.group())

            participant_parent = None
            if len(centers) >= 3:
                participant_parent = centers[2]
            elif len(centers) >= 2:
                participant_parent = centers[1]

            fonts = participant_parent.find_all("font") if participant_parent else []
            if not fonts:
                fonts = td.find_all("font")

            male_total = 0
            female_total = 0
            single_total = 0
            valid_texts: List[str] = []

            for font in fonts:
                text = font.get_text(strip=True)
                if not text:
                    continue
                if _should_exclude_text(text, cfg.exclude_keywords):
                    LOGGER.debug(
                        "Excluded text '%s' for day %s due to keyword filter (スタッフ除外対象外).",
                        text,
                        day,
                    )
                    continue
                male_count, female_count, single_count = _count_participant_line(text)
                male_total += male_count
                female_total += female_count
                single_total += single_count
                valid_texts.append(text)

            dow = DOW_EN[col_index % len(DOW_EN)]
            total = male_total + female_total
            ratio = (female_total / total) if total else 0.0
            entry = {
                "day": day,
                "dow_index": col_index % len(DOW_EN),
                "dow": dow,
                "dow_en": dow,
                "male": male_total,
                "female": female_total,
                "single_female": single_total,
                "total": total,
                "ratio": ratio,
                "entries": valid_texts,
            }
            LOGGER.debug("Parsed entry: %s", entry)
            results.append(entry)

    results.sort(key=lambda item: item["day"])
    return results


def evaluate_conditions(
    stats: Sequence[Dict[str, Any]],
    settings: Optional[Settings] = None,
) -> List[Dict[str, Any]]:
    """Evaluate thresholds and mark whether each day meets alert conditions."""

    cfg = settings or SETTINGS
    evaluated: List[Dict[str, Any]] = []
    ratio_threshold = max(0.40, cfg.female_ratio_min)
    for entry in stats:
        considered = True
        dow_value = entry.get("dow") or entry.get("dow_en")
        if cfg.include_dow and dow_value not in cfg.include_dow:
            considered = False
        if cfg.min_total is not None and entry.get("total", 0) < cfg.min_total:
            considered = False
        ratio = entry.get("ratio", 0.0)
        required_single = 5 if dow_value in {"Fri", "Sat"} else 3
        female_total = entry.get("female", 0)
        single_total = entry.get("single_female", 0)
        meets = (
            considered
            and entry.get("total", 0) > 0
            and single_total >= required_single
            and female_total >= max(cfg.female_min, required_single)
            and ratio >= ratio_threshold
        )
        updated = dict(entry)
        updated["ratio"] = round(ratio, 3)
        updated["dow"] = dow_value
        updated["considered"] = considered
        updated["meets"] = meets
        updated["required_single_female"] = required_single
        updated["ratio_threshold"] = ratio_threshold
        evaluated.append(updated)
        LOGGER.debug("Evaluated entry: %s", updated)
    return evaluated


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
    entry: Dict[str, Any],
    prev_state: Optional[Dict[str, Any]],
    *,
    now: int,
    cooldown_seconds: int,
) -> Tuple[Optional[str], str, Optional[int]]:
    """Determine notification stage transitions and actions for a day."""

    meets = bool(entry.get("meets"))
    single = entry.get("single_female", 0)
    ratio = entry.get("ratio", 0.0)
    required_single = entry.get("required_single_female", 0) or 0

    prev_stage = _coerce_stage(prev_state.get("stage")) if prev_state else "none"
    prev_last_notified = _coerce_last_notified(prev_state.get("last_notified_at")) if prev_state else None

    stage = prev_stage
    last_notified = prev_last_notified
    action: Optional[str] = None

    if not meets:
        stage = "none"
        return action, stage, last_notified

    bonus_by_single = single >= required_single + 2
    bonus_by_ratio = ratio >= 0.50

    if stage == "none":
        action = "initial"
        stage = "initial"
        last_notified = now
    elif stage == "initial":
        if bonus_by_single or bonus_by_ratio:
            action = "bonus"
            stage = "bonus"
            last_notified = now
    elif stage == "bonus":
        if last_notified is None or now - last_notified >= cooldown_seconds:
            stage = "initial"
        else:
            # Cooldown in progress; stay silent.
            stage = "bonus"
    else:
        stage = "initial"
        action = "initial"
        last_notified = now

    return action, stage, last_notified


def _format_entry(
    entry: Dict[str, Any],
    *,
    include_male: bool = False,
    markdown: bool = True,
) -> str:
    """Return a human-readable representation of the entry."""

    percent = int(round(entry.get("ratio", 0.0) * 100))
    dow_value = entry.get("dow") or entry.get("dow_en")
    label = f"{entry['day']}日({dow_value})"
    if markdown:
        label = f"*{label}*"
    components = [f"単女{entry.get('single_female', 0)}", f"女{entry.get('female', 0)}"]
    if include_male:
        components.append(f"男{entry.get('male', 0)}")
    components.append(f"全{entry.get('total', 0)}")
    detail = " ".join(components)
    return f"{label}: {detail} ({percent}%)"


def _format_stage_notification(
    entry: Dict[str, Any],
    *,
    markdown: bool,
) -> str:
    label_map = {"initial": "初回", "bonus": "追加"}
    notification_type = entry.get("notification_type", "")
    label_text = label_map.get(notification_type, notification_type or "通知")
    dow_value = entry.get("dow") or entry.get("dow_en")
    day_label = f"{entry['day']}日({dow_value})"
    if markdown:
        day_label = f"*{day_label}*"
    percent = int(round(entry.get("ratio", 0.0) * 100))
    return (
        f"{day_label}: [{label_text}] 単女{entry.get('single_female', 0)} "
        f"女{entry.get('female', 0)}/全{entry.get('total', 0)} ({percent}%)"
    )


def _build_slack_payload(
    text: str,
    newly_met: Sequence[Dict[str, Any]],
    changed_counts: Sequence[Dict[str, Any]],
    settings: Settings,
    stage_notifications: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    blocks: List[Dict[str, Any]] = []
    stage_notifications = list(stage_notifications or [])
    if stage_notifications:
        lines = "\n".join(
            f"• {_format_stage_notification(entry, markdown=True)}"
            for entry in stage_notifications
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*基準達成通知*"},
            }
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})
    if newly_met:
        lines = "\n".join(
            f"• {_format_entry(entry, markdown=True)}" for entry in newly_met
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*新規で条件を満たした日*"},
            }
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})
    if changed_counts:
        lines = "\n".join(
            f"• {_format_entry(entry, include_male=True, markdown=True)}" for entry in changed_counts
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*人数が更新された日*"},
            }
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})
    if blocks:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "月間カレンダーを開く"},
                        "url": settings.target_url,
                    }
                ],
            }
        )
    return {"text": text, "blocks": blocks} if blocks else {"text": text}


def notify_slack(payload: Dict[str, Any], fallback_text: str, settings: Settings) -> None:
    """Send notification to Slack with fallback to text-only payload."""

    if not settings.slack_webhook_url:
        LOGGER.warning("SLACK_WEBHOOK_URL not set. Skipping Slack notification. Message:\n%s", fallback_text)
        return
    fallback_attempted = False
    try:
        response = requests.post(settings.slack_webhook_url, json=payload, timeout=10)
        if response.status_code >= 400 and "blocks" in payload:
            LOGGER.error("Slack responded with %s. Falling back to text payload.", response.status_code)
            fallback_attempted = True
            requests.post(settings.slack_webhook_url, json={"text": fallback_text}, timeout=10)
        response.raise_for_status()
    except Exception as exc:  # pragma: no cover - depends on network
        LOGGER.error("Slack notification failed: %s", exc)
        if "blocks" in payload and not fallback_attempted:
            try:
                requests.post(settings.slack_webhook_url, json={"text": fallback_text}, timeout=10)
            except Exception as fallback_exc:  # pragma: no cover
                LOGGER.error("Slack fallback notification failed: %s", fallback_exc)


def diff_changes(
    prev_days: Dict[str, Any],
    curr_stats: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return tuples of changed counts, newly met, and status changes."""

    changed_counts: List[Dict[str, Any]] = []
    newly_met: List[Dict[str, Any]] = []
    meets_changed: List[Dict[str, Any]] = []

    for entry in curr_stats:
        if not entry.get("considered", True):
            LOGGER.debug("Skipping day %s for diff calculations (considered=False).", entry.get("day"))
            continue
        key = str(entry["day"])
        prev_raw = prev_days.get(key) if isinstance(prev_days, dict) else None
        prev = prev_raw if isinstance(prev_raw, dict) else None
        prev_meets = bool(prev.get("meets")) if prev else False
        meets_now = bool(entry.get("meets"))
        if prev is None and entry.get("total", 0) > 0:
            changed_counts.append(entry)
        elif prev and (
            entry.get("male") != prev.get("male")
            or entry.get("female") != prev.get("female")
            or entry.get("total") != prev.get("total")
            or entry.get("single_female") != prev.get("single_female")
        ):
            changed_counts.append(entry)
        if not prev_meets and meets_now:
            newly_met.append(entry)
        if prev is None or prev_meets != meets_now:
            meets_changed.append(entry)
    return changed_counts, newly_met, meets_changed


def build_fallback_text(
    newly_met: Sequence[Dict[str, Any]],
    changed_counts: Sequence[Dict[str, Any]],
    settings: Settings,
    *,
    stage_notifications: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    lines: List[str] = []
    stage_notifications = list(stage_notifications or [])
    if stage_notifications:
        lines.append("【基準達成通知】")
        lines.extend(
            f"- {_format_stage_notification(entry, markdown=False)}"
            for entry in stage_notifications
        )
    if newly_met:
        lines.append("【新規で条件を満たした日】")
        lines.extend(
            f"- {_format_entry(entry, markdown=False)}" for entry in newly_met
        )
    if changed_counts:
        lines.append("【人数が更新された日】")
        lines.extend(
            f"- {_format_entry(entry, include_male=True, markdown=False)}"
            for entry in changed_counts
        )
    lines.append(f"URL: {settings.target_url}")
    return "\n".join(lines)


def _summary_lines(stats: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(
        _format_entry(entry, include_male=True, markdown=False)
        for entry in sorted(stats, key=lambda item: item["day"])[:10]
    )


def run_notifications(
    stats: Sequence[Dict[str, Any]],
    changed_counts: Sequence[Dict[str, Any]],
    newly_met: Sequence[Dict[str, Any]],
    settings: Settings,
    *,
    stage_notifications: Sequence[Dict[str, Any]] = (),
) -> None:
    """Send Slack notifications based on notify mode and debug flags."""

    stage_notifications = list(stage_notifications)
    if stage_notifications:
        stage_days = {entry.get("day") for entry in stage_notifications}
        filtered_newly = [
            entry for entry in newly_met if entry.get("day") not in stage_days
        ]
        include_changed = changed_counts if settings.notify_mode == "changed" else []
        fallback_text = build_fallback_text(
            filtered_newly,
            include_changed,
            settings,
            stage_notifications=stage_notifications,
        )
        payload = _build_slack_payload(
            fallback_text,
            filtered_newly,
            include_changed,
            settings,
            stage_notifications=stage_notifications,
        )
        notify_slack(payload, fallback_text, settings)
        return

    if settings.notify_mode == "newly":
        if newly_met:
            fallback_text = build_fallback_text(newly_met, [], settings)
            payload = _build_slack_payload(fallback_text, newly_met, [], settings)
            notify_slack(payload, fallback_text, settings)
        else:
            LOGGER.info("No newly satisfied days. Skipping notification.")
    else:  # changed mode
        if newly_met or changed_counts:
            fallback_text = build_fallback_text(newly_met, changed_counts, settings)
            payload = _build_slack_payload(
                fallback_text, newly_met, changed_counts, settings
            )
            notify_slack(payload, fallback_text, settings)
        else:
            LOGGER.info("No changes detected for notification.")

    if settings.debug_summary:
        summary_text = "【デバッグサマリー（上位10日）】\n" + _summary_lines(stats)
        payload = {
            "text": summary_text,
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*デバッグサマリー（上位10日）*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(
                    f"• {_format_entry(entry, include_male=True)}" for entry in stats[:10]
                )}},
            ],
        }
        notify_slack(payload, summary_text, settings)


async def run() -> int:
    """Entry point for the watcher."""

    settings = SETTINGS
    state = load_state()

    skip, headers = should_skip_by_http_headers(settings, state)
    if skip:
        LOGGER.info("Skipping fetch due to matching ETag/Last-Modified.")
        return 0

    delay = 1.0
    html = ""
    for attempt in range(3):
        try:
            html = await fetch_calendar_html(settings)
            break
        except Exception as exc:
            LOGGER.error("Fetch attempt %s failed: %s", attempt + 1, exc)
            if attempt == 2:
                fallback_text = f"[ERROR] fetch failed: {exc}"
                notify_slack({"text": fallback_text}, fallback_text, settings)
                return 1
            time.sleep(delay)
            delay = delay * 2 + 1
    else:
        return 1

    parsed_stats = parse_day_entries(html, settings)
    print(
        "[DEBUG] parsed first days:",
        [
            f"{s['day']}日({s['dow']}): 単女{s.get('single_female', 0)} 女{s.get('female', 0)} "
            f"男{s.get('male', 0)} 全{s.get('total', 0)} ({int(s.get('ratio', 0.0) * 100)}%)"
            for s in parsed_stats[:10]
        ],
    )
    stats = evaluate_conditions(parsed_stats, settings)
    LOGGER.info("Parsed %d day entries.", len(stats))
    LOGGER.debug("Stats preview: %s", stats[:10])

    prev_days = state.get("days", {}) if isinstance(state, dict) else {}
    changed_counts, newly_met, meets_changed = diff_changes(prev_days, stats)

    now_ts = int(time.time())
    cooldown_seconds = max(0, settings.cooldown_minutes) * 60
    stage_notifications: List[Dict[str, Any]] = []
    new_days: Dict[str, Any] = {}

    for entry in stats:
        key = str(entry["day"])
        prev_raw = prev_days.get(key) if isinstance(prev_days, dict) else None
        prev_dict = prev_raw if isinstance(prev_raw, dict) else None
        prev_stage = _coerce_stage(prev_dict.get("stage")) if prev_dict else "none"
        prev_last = (
            _coerce_last_notified(prev_dict.get("last_notified_at")) if prev_dict else None
        )
        action, stage, last_notified = evaluate_stage_transition(
            entry,
            prev_dict,
            now=now_ts,
            cooldown_seconds=cooldown_seconds,
        )
        if LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug(
                "Stage[%s] %s -> %s action=%s last_notified_at=%s single=%s ratio=%.3f",
                key,
                prev_stage,
                stage,
                action,
                last_notified,
                entry.get("single_female", 0),
                entry.get("ratio", 0.0),
            )
        if action:
            stage_entry = dict(entry)
            stage_entry["notification_type"] = action
            stage_notifications.append(stage_entry)

        new_days[key] = {
            "male": entry["male"],
            "female": entry["female"],
            "single_female": entry.get("single_female", 0),
            "total": entry["total"],
            "ratio": entry["ratio"],
            "meets": entry["meets"],
            "dow": entry.get("dow") or entry.get("dow_en"),
            "dow_en": entry.get("dow_en") or entry.get("dow"),
            "considered": entry["considered"],
            "required_single_female": entry.get("required_single_female"),
            "ratio_threshold": entry.get("ratio_threshold"),
            "stage": stage,
            "last_notified_at": last_notified if last_notified is not None else prev_last,
        }
    state.update({"etag": headers.get("etag"), "last_modified": headers.get("last_modified"), "days": new_days})
    save_state(state)

    run_notifications(
        stats,
        changed_counts,
        newly_met,
        settings,
        stage_notifications=stage_notifications,
    )

    LOGGER.info(
        "Notification summary: changed=%d newly_met=%d status_changed=%d",
        len(changed_counts),
        len(newly_met),
        len(meets_changed),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    _configure_logging()
    raise SystemExit(asyncio.run(run()))
