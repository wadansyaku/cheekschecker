"""Monitor the monthly calendar and notify Slack when female participation meets thresholds."""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
    """Fetch the calendar HTML using Playwright."""
    LOGGER.info("Fetching calendar HTML from %s", settings.target_url)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"
            )
        )
        page = await context.new_page()
        try:
            await page.goto(settings.target_url, wait_until="networkidle", timeout=60_000)
            html = await page.content()
            LOGGER.debug("Fetched %d characters of HTML", len(html))
            return html
        finally:
            await context.close()
            await browser.close()


def _font_texts_from_cell(td) -> List[str]:
    texts: List[str] = []
    centers = td.find_all("center")
    font_parent = None
    if len(centers) >= 3:
        font_parent = centers[2]
    elif len(centers) >= 2:
        font_parent = centers[1]
    if font_parent:
        fonts = font_parent.find_all("font")
    else:
        fonts = []
    if not fonts:
        fonts = [font for font in td.find_all("font") if font.find_parent("a")]
    for font in fonts:
        text = font.get_text(strip=True)
        if text:
            texts.append(text)
    return texts


def parse_day_entries(html: str, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Parse the calendar HTML and extract daily participation entries.

    Parameters
    ----------
    html:
        Raw HTML string fetched from the monthly calendar.
    settings:
        Optional Settings instance to determine exclusion keywords. Defaults to module SETTINGS.

    Returns
    -------
    list of dicts with day statistics sorted by day.
    """

    cfg = settings or SETTINGS
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table[border='2']")
    if not table:
        LOGGER.warning("Target table not found in HTML.")
        return []

    results: List[Dict[str, Any]] = []
    exclude_keywords = cfg.exclude_keywords

    for tr in table.find_all("tr"):
        tds = tr.find_all("td", attrs={"valign": "top"})
        for idx, td in enumerate(tds):
            centers = td.find_all("center")
            if not centers:
                continue
            day_text = centers[0].get_text(strip=True)
            if not day_text.isdigit():
                continue
            day = int(day_text)
            texts = _font_texts_from_cell(td)
            male = 0
            female = 0
            valid_texts: List[str] = []
            for text in texts:
                lowered = text.lower()
                if any(keyword in lowered for keyword in exclude_keywords):
                    LOGGER.debug("Excluded text '%s' for day %s due to keyword filter.", text, day)
                    continue
                male += text.count("♂")
                female += text.count("♀")
                valid_texts.append(text)
            total = male + female
            ratio = (female / total) if total else 0.0
            entry = {
                "day": day,
                "dow_index": idx % len(DOW_EN),
                "dow_en": DOW_EN[idx % len(DOW_EN)],
                "male": male,
                "female": female,
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
    for entry in stats:
        considered = True
        if cfg.include_dow and entry.get("dow_en") not in cfg.include_dow:
            considered = False
        if cfg.min_total is not None and entry.get("total", 0) < cfg.min_total:
            considered = False
        ratio = entry.get("ratio", 0.0)
        meets = (
            considered
            and entry.get("female", 0) >= cfg.female_min
            and entry.get("total", 0) > 0
            and ratio >= cfg.female_ratio_min
        )
        updated = dict(entry)
        updated["ratio"] = round(ratio, 3)
        updated["considered"] = considered
        updated["meets"] = meets
        evaluated.append(updated)
        LOGGER.debug("Evaluated entry: %s", updated)
    return evaluated


def _format_entry(entry: Dict[str, Any], include_male: bool = False) -> str:
    percent = round(entry.get("ratio", 0.0) * 100)
    base = f"{entry['day']}日({entry['dow_en']})"
    if include_male:
        base += f": 男{entry['male']} 女{entry['female']}/全{entry['total']} ({percent}%)"
    else:
        base += f": 女{entry['female']}/全{entry['total']} ({percent}%)"
    return base


def _build_slack_payload(
    text: str,
    newly_met: Sequence[Dict[str, Any]],
    changed_counts: Sequence[Dict[str, Any]],
    settings: Settings,
) -> Dict[str, Any]:
    blocks: List[Dict[str, Any]] = []
    if newly_met:
        lines = "\n".join(f"• {_format_entry(entry)}" for entry in newly_met)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*新規で条件を満たした日*"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})
    if changed_counts:
        lines = "\n".join(f"• {_format_entry(entry, include_male=True)}" for entry in changed_counts)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*人数が更新された日*"}})
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
        prev = prev_days.get(key) if isinstance(prev_days, dict) else None
        prev_meets = bool(prev.get("meets")) if prev else False
        meets_now = bool(entry.get("meets"))
        if prev is None and entry.get("total", 0) > 0:
            changed_counts.append(entry)
        elif prev and (
            entry.get("male") != prev.get("male")
            or entry.get("female") != prev.get("female")
            or entry.get("total") != prev.get("total")
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
) -> str:
    lines: List[str] = []
    if newly_met:
        lines.append("【新規で条件を満たした日】")
        lines.extend(f"- {_format_entry(entry)}" for entry in newly_met)
    if changed_counts:
        lines.append("【人数が更新された日】")
        lines.extend(f"- {_format_entry(entry, include_male=True)}" for entry in changed_counts)
    lines.append(f"URL: {settings.target_url}")
    return "\n".join(lines)


def _summary_lines(stats: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(
        _format_entry(entry, include_male=True)
        for entry in sorted(stats, key=lambda item: item["day"])[:10]
    )


def run_notifications(
    stats: Sequence[Dict[str, Any]],
    changed_counts: Sequence[Dict[str, Any]],
    newly_met: Sequence[Dict[str, Any]],
    settings: Settings,
) -> None:
    """Send Slack notifications based on notify mode and debug flags."""

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
            payload = _build_slack_payload(fallback_text, newly_met, changed_counts, settings)
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

    stats = evaluate_conditions(parse_day_entries(html, settings), settings)
    print("[DEBUG] parsed first days:",
      [f"{s['day']}日: 男{s['male']} 女{s['female']} 全{s['total']} ({int(s['ratio']*100)}%)"
       for s in stats[:10]])
    LOGGER.info("Parsed %d day entries.", len(stats))
    LOGGER.debug("Stats preview: %s", stats[:10])

    prev_days = state.get("days", {}) if isinstance(state, dict) else {}
    changed_counts, newly_met, meets_changed = diff_changes(prev_days, stats)

    new_days = {
        str(entry["day"]): {
            "male": entry["male"],
            "female": entry["female"],
            "total": entry["total"],
            "ratio": entry["ratio"],
            "meets": entry["meets"],
            "dow_en": entry["dow_en"],
            "considered": entry["considered"],
        }
        for entry in stats
    }
    state.update({"etag": headers.get("etag"), "last_modified": headers.get("last_modified"), "days": new_days})
    save_state(state)

    if settings.notify_mode == "newly":
        run_notifications(stats, [], newly_met, settings)
    else:
        run_notifications(stats, changed_counts, newly_met, settings)

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
