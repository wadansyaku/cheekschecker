#!/usr/bin/env python3
"""Generate weekly/monthly summaries with masked archives and Slack output."""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from zoneinfo import ZoneInfo

from src.logging_config import get_logger

LOGGER = get_logger(__name__)
JST = ZoneInfo("Asia/Tokyo")

DOW_JP = ["月", "火", "水", "木", "金", "土", "日"]
DOW_ORDER = [0, 1, 2, 3, 4, 5, 6]

STEP_SUMMARY_TITLES = {
    "weekly": "Cheeks Weekly Summary",
    "monthly": "Cheeks Monthly Summary",
}

MASK_COUNT_BANDS: List[Tuple[int, Optional[int], str]] = [
    (0, 0, "0"),
    (1, 1, "1"),
    (2, 2, "2"),
    (3, 4, "3-4"),
    (5, 6, "5-6"),
    (7, 8, "7-8"),
    (9, None, "9+"),
]
MASK_TOTAL_BANDS: List[Tuple[int, Optional[int], str]] = [
    (0, 9, "<10"),
    (10, 19, "10-19"),
    (20, 29, "20-29"),
    (30, 49, "30-49"),
    (50, None, "50+"),
]
MASK_RATIO_BANDS: List[Tuple[float, Optional[float], str]] = [
    (0.0, 0.39, "<40%"),
    (0.40, 0.49, "40±"),
    (0.50, 0.59, "50±"),
    (0.60, 0.69, "60±"),
    (0.70, 0.79, "70±"),
    (0.80, None, "80+%"),
]


@dataclass(frozen=True)
class DailyRecord:
    business_day: date
    single_female: int
    female: int
    total: int
    ratio: float

    @property
    def weekday(self) -> int:
        return self.business_day.weekday()


@dataclass(frozen=True)
class RawDataset:
    period_label: str
    current: List[DailyRecord]
    previous: List[DailyRecord]


@dataclass(frozen=True)
class SummaryContext:
    period_key: str
    period_label: str
    period_start: date
    period_end: date
    current: List[DailyRecord]
    previous: List[DailyRecord]
    stats: Dict[str, Dict[str, float]]
    top_days: List[DailyRecord]
    weekday_profile: Dict[int, Dict[str, float]]
    trend: Dict[str, Optional[float]]

    @property
    def day_count(self) -> int:
        return len(self.current)

    @property
    def previous_day_count(self) -> int:
        return len(self.previous)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


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
                handle.write(f"{fallback or 'No data'}\n\n")
    except OSError as exc:  # pragma: no cover - filesystem nuances
        LOGGER.debug("Failed to append step summary: %s", exc)


def _parse_iso_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    else:
        parsed = parsed.astimezone(JST)
    return parsed.date()


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _record_from_dict(data: Dict[str, Any]) -> Optional[DailyRecord]:
    business_day = _parse_iso_date(data.get("date"))
    if business_day is None:
        return None
    single = _coerce_int(data.get("single_female"))
    female = _coerce_int(data.get("female"))
    total = _coerce_int(data.get("total"), female)
    if total <= 0:
        total = max(female, single)
    ratio_raw = data.get("ratio")
    ratio = _coerce_float(ratio_raw, 0.0)
    if ratio <= 0 and total:
        ratio = female / total
    ratio = max(0.0, min(1.0, ratio))
    return DailyRecord(
        business_day=business_day,
        single_female=max(0, single),
        female=max(0, female),
        total=max(0, total),
        ratio=ratio,
    )


def load_raw_dataset(path: Optional[Path]) -> RawDataset:
    if not path or not path.exists():
        LOGGER.warning("Raw dataset is missing: %s", path)
        return RawDataset(period_label="", current=[], previous=[])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        LOGGER.error("Failed to decode raw dataset %s: %s", path, exc)
        return RawDataset(period_label="", current=[], previous=[])
    current = [_record_from_dict(item) for item in raw.get("days", [])]
    previous = [_record_from_dict(item) for item in raw.get("previous_days", [])]
    current = [item for item in current if item is not None]
    previous = [item for item in previous if item is not None]
    period_label = str(raw.get("period_label") or "").strip()
    return RawDataset(period_label=period_label, current=current, previous=previous)


def _safe_mean(values: Sequence[float]) -> Optional[float]:
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None
    return statistics.mean(cleaned)


def _safe_median(values: Sequence[float]) -> Optional[float]:
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None
    return statistics.median(cleaned)


def _safe_max(values: Sequence[float]) -> Optional[float]:
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None
    return max(cleaned)


def _calc_stats(records: Sequence[DailyRecord]) -> Dict[str, Dict[str, Optional[float]]]:
    singles = [float(r.single_female) for r in records]
    females = [float(r.female) for r in records]
    totals = [float(r.total) for r in records]
    ratios = [r.ratio * 100 for r in records]

    def _bundle(values: Sequence[float]) -> Dict[str, Optional[float]]:
        return {
            "average": _safe_mean(values),
            "median": _safe_median(values),
            "max": _safe_max(values),
        }

    return {
        "single": _bundle(singles),
        "female": _bundle(females),
        "total": _bundle(totals),
        "ratio": _bundle(ratios),
    }


def _calc_top_days(records: Sequence[DailyRecord], limit: int = 3) -> List[DailyRecord]:
    ordered = sorted(
        records,
        key=lambda r: (
            r.single_female,
            r.female,
            r.ratio,
            r.total,
            r.business_day.toordinal(),
        ),
        reverse=True,
    )
    return ordered[:limit]


def _calc_weekday_profile(records: Sequence[DailyRecord]) -> Dict[int, Dict[str, float]]:
    buckets: Dict[int, List[DailyRecord]] = {}
    for record in records:
        buckets.setdefault(record.weekday, []).append(record)
    profile: Dict[int, Dict[str, float]] = {}
    for weekday, items in buckets.items():
        stats = _calc_stats(items)
        profile[weekday] = {
            "single": stats["single"]["average"],
            "female": stats["female"]["average"],
            "ratio": stats["ratio"]["average"],
            "total": stats["total"]["average"],
        }
    return profile


def _calc_trend(
    current: Dict[str, Dict[str, Optional[float]]],
    previous: Dict[str, Dict[str, Optional[float]]],
) -> Dict[str, Optional[float]]:
    trend: Dict[str, Optional[float]] = {}
    for key in ("single", "female", "ratio"):
        current_avg = current[key]["average"]
        prev_avg = previous[key]["average"]
        if current_avg is None or prev_avg is None:
            trend[key] = None
            continue
        trend[key] = current_avg - prev_avg
    return trend


def build_summary_context(period_key: str, dataset: RawDataset) -> Optional[SummaryContext]:
    if not dataset.current:
        return None
    period_start = min(r.business_day for r in dataset.current)
    period_end = max(r.business_day for r in dataset.current)
    stats_current = _calc_stats(dataset.current)
    stats_previous = _calc_stats(dataset.previous)
    trend = _calc_trend(stats_current, stats_previous)
    context = SummaryContext(
        period_key=period_key,
        period_label=dataset.period_label or period_key,
        period_start=period_start,
        period_end=period_end,
        current=dataset.current,
        previous=dataset.previous,
        stats=stats_current,
        top_days=_calc_top_days(dataset.current),
        weekday_profile=_calc_weekday_profile(dataset.current),
        trend=trend,
    )
    return context


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


def _mask_count(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return _bin_value(int(round(value)), MASK_COUNT_BANDS)


def _mask_total(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return _bin_value(int(round(value)), MASK_TOTAL_BANDS)


def _mask_ratio(value: Optional[float]) -> str:
    if value is None:
        return "-"
    normalized = max(0.0, min(1.0, value / 100.0))
    return _bin_ratio(normalized, MASK_RATIO_BANDS)


def _weekday_label(weekday: int) -> str:
    if 0 <= weekday < len(DOW_JP):
        return DOW_JP[weekday]
    return "?"


def _format_day(record: DailyRecord) -> str:
    label = _weekday_label(record.weekday)
    return f"{record.business_day.day}日({label})"


def _format_ratio_percent(value: float) -> str:
    return f"{round(value):d}%"


def _format_trend_value(key: str, diff: Optional[float]) -> str:
    if diff is None:
        return "比較対象なし"
    if key == "ratio":
        diff_value = round(diff)
        arrow = "↗" if diff_value > 0 else "↘" if diff_value < 0 else "→"
        return f"{arrow} {diff_value:+d}pp"
    diff_value = round(diff, 1)
    arrow = "↗" if diff_value > 0 else "↘" if diff_value < 0 else "→"
    return f"{arrow} {diff_value:+.1f}"


def _build_summary_actions_block() -> Dict[str, Any]:
    repo = os.getenv("GITHUB_REPOSITORY", "wadansyaku/cheekschecker")
    base_url = f"https://raw.githubusercontent.com/{repo}/main"
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "history_masked.json"},
                "url": f"{base_url}/history_masked.json",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "summary_masked.json"},
                "url": f"{base_url}/summary_masked.json",
            },
        ],
    }


def build_slack_payload(
    context: SummaryContext, title: str, logical_today: Optional[date] = None
) -> Tuple[Dict[str, Any], str, List[Tuple[str, List[str]]]]:
    """Generate Slack payload and step-summary sections for the summary."""

    def _fmt_range(start: date, end: date) -> str:
        start_label = f"{start.month:02d}/{start.day:02d}({_weekday_label(start.weekday())})"
        end_label = f"{end.month:02d}/{end.day:02d}({_weekday_label(end.weekday())})"
        return f"{start_label}〜{end_label}"

    def _fmt_ratio(value: Optional[float]) -> str:
        if value is None:
            return "-"
        return f"{value:.1f}%"

    def _fmt_count(value: Optional[float]) -> str:
        if value is None:
            return "-"
        if abs(value - round(value)) < 0.01:
            return f"{int(round(value))}"
        return f"{value:.1f}"

    def _latest_record(records: Sequence[DailyRecord]) -> DailyRecord:
        return max(records, key=lambda r: r.business_day)

    stats_single = context.stats["single"]
    stats_female = context.stats["female"]
    stats_ratio = context.stats["ratio"]

    logical_ref = logical_today or context.period_end
    today_entry = next((r for r in context.current if r.business_day == logical_ref), None)
    latest = today_entry or _latest_record(context.current)
    latest_label = f"{latest.business_day.month:02d}/{latest.business_day.day:02d}({_weekday_label(latest.weekday)})"
    latest_percent = int(round(latest.ratio * 100))
    latest_field_lines = [
        f"{latest_label}",
        f"単女{latest.single_female} 女{latest.female}/全{latest.total}",
        f"比率{latest_percent}%",
    ]

    recent_field_lines = [
        f"平均 単女{_fmt_count(stats_single['average'])} 女{_fmt_count(stats_female['average'])} 比率{_fmt_ratio(stats_ratio['average'])}",
        f"中央値 単女{_fmt_count(stats_single['median'])} 女{_fmt_count(stats_female['median'])} 比率{_fmt_ratio(stats_ratio['median'])}",
        f"最大 単女{_fmt_count(stats_single['max'])} 女{_fmt_count(stats_female['max'])} 比率{_fmt_ratio(stats_ratio['max'])}",
    ]

    top_lines = [
        f"• {_format_day(record)} 単女{record.single_female} 女{record.female}/全{record.total} ({_format_ratio_percent(record.ratio * 100)})"
        for record in context.top_days
    ]
    if not top_lines:
        top_lines.append("• 該当なし")

    trend_line = (
        "• 傾向: "
        f"単女{_format_trend_value('single', context.trend.get('single'))} / "
        f"女{_format_trend_value('female', context.trend.get('female'))} / "
        f"比率{_format_trend_value('ratio', context.trend.get('ratio'))}"
    )

    weekday_parts: List[str] = []
    for weekday in DOW_ORDER:
        if weekday not in context.weekday_profile:
            continue
        profile = context.weekday_profile[weekday]
        weekday_parts.append(
            f"{_weekday_label(weekday)} 単{_fmt_count(profile['single'])} 女{_fmt_count(profile['female'])} ({_fmt_ratio(profile['ratio'])})"
        )
    weekday_line = "• 曜日: " + (" / ".join(weekday_parts) if weekday_parts else "データ不足")

    range_text = _fmt_range(context.period_start, context.period_end)
    context_elements = [
        {"type": "mrkdwn", "text": f"対象期間: {range_text}"},
        {"type": "mrkdwn", "text": f"営業日数: {context.day_count}日"},
    ]
    if context.previous_day_count:
        context_elements.append(
            {
                "type": "mrkdwn",
                "text": (
                    "直前対比: "
                    f"単女{_format_trend_value('single', context.trend.get('single'))} / "
                    f"比率{_format_trend_value('ratio', context.trend.get('ratio'))}"
                ),
            }
        )

    actions_block = _build_summary_actions_block()

    full_title = title if title.startswith('Cheekschecker') else f'Cheekschecker {title}'

    slack_blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": full_title, "emoji": False}},
        {"type": "context", "elements": context_elements},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*今日*\n" + "\n".join(latest_field_lines)},
                {"type": "mrkdwn", "text": "*近日*\n" + "\n".join(recent_field_lines)},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Top3 / 傾向 / 曜日*\n" + "\n".join(top_lines + [trend_line, weekday_line]),
            },
        },
        actions_block,
    ]
    fallback_lines = [
        full_title,
        f"対象期間 {range_text} / 営業日数 {context.day_count}日",
        "今日: " + ", ".join(latest_field_lines[1:]),
        "近日: " + "; ".join(recent_field_lines),
    ]
    fallback_lines.extend(line.replace("• ", "") for line in top_lines)
    fallback_lines.append(trend_line.replace("• ", ""))
    fallback_lines.append(weekday_line.replace("• ", ""))
    fallback_text = "\n".join(fallback_lines)

    summary_sections: List[Tuple[str, List[str]]] = [
        ("今日", [", ".join(latest_field_lines)]),
        ("近日", recent_field_lines),
        (
            "Top3/傾向/曜日",
            [line.replace("• ", "") for line in top_lines]
            + [trend_line.replace("• ", ""), weekday_line.replace("• ", "")],
        ),
    ]

    headline_text = f"*対象期間*: {range_text}\n*営業日数*: {context.day_count}日"
    compat_blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": full_title, "emoji": False}},
        {"type": "section", "text": {"type": "mrkdwn", "text": headline_text}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*今日*\n" + "\n".join(latest_field_lines)},
                {"type": "mrkdwn", "text": "*近日*\n" + "\n".join(recent_field_lines)},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Hot day Top3*\n" + "\n".join(top_lines)},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*傾向 / 曜日*\n" + "\n".join([trend_line.replace("• ", "• "), weekday_line.replace("• ", "• ")]),
            },
        },
    ]

    payload: Dict[str, Any] = {
        "text": fallback_text,
        "blocks": compat_blocks,
        "_summary_sections": summary_sections,
        "_slack_blocks": slack_blocks,
    }
    return payload, fallback_text


def build_placeholder_summary_payload(
    title: str, message: str
) -> Tuple[Dict[str, Any], str, List[Tuple[str, List[str]]]]:
    actions_block = _build_summary_actions_block()
    fallback = f"{title} {message}".strip()
    sections: List[Tuple[str, List[str]]] = [
        ("今日", ["該当なし"]),
        ("近日", ["該当なし"]),
        ("Top3/傾向/曜日", [message]),
    ]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": False}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": message}]},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*今日*\n該当なし"},
                {"type": "mrkdwn", "text": "*近日*\n該当なし"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Top3 / 傾向 / 曜日*\n" + message},
        },
        actions_block,
    ]
    payload = {"text": fallback, "blocks": blocks}
    return payload, fallback, sections


def _load_masked_history(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"mask_level": 1}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.warning("history_masked.json is invalid; fallback to defaults")
        return {"mask_level": 1}


def _load_summary_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.warning("Existing summary store is invalid; recreating %s", path)
        return {}


def _save_summary_store(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _trend_direction(diff: Optional[float]) -> str:
    if diff is None:
        return "unknown"
    if diff > 0.5:
        return "up"
    if diff < -0.5:
        return "down"
    return "flat"


def build_masked_summary(context: Optional[SummaryContext], *, history_meta: Dict[str, Any]) -> Dict[str, Any]:
    generated_at = datetime.now(tz=JST).isoformat()
    masked: Dict[str, Any] = {
        "generated_at": generated_at,
        "mask_level": history_meta.get("mask_level", 1),
    }
    if context is None:
        masked.update({"status": "no-data"})
        return masked
    stats = context.stats
    masked.update(
        {
            "status": "ok",
            "period_start": context.period_start.isoformat(),
            "period_end": context.period_end.isoformat(),
            "day_count": context.day_count,
            "stats": {
                "single": {
                    "average": _mask_count(stats["single"]["average"]),
                    "median": _mask_count(stats["single"]["median"]),
                    "max": _mask_count(stats["single"]["max"]),
                },
                "female": {
                    "average": _mask_count(stats["female"]["average"]),
                    "median": _mask_count(stats["female"]["median"]),
                    "max": _mask_count(stats["female"]["max"]),
                },
                "total": {
                    "average": _mask_total(stats["total"]["average"]),
                    "median": _mask_total(stats["total"]["median"]),
                    "max": _mask_total(stats["total"]["max"]),
                },
                "ratio": {
                    "average": _mask_ratio(stats["ratio"]["average"]),
                    "median": _mask_ratio(stats["ratio"]["median"]),
                    "max": _mask_ratio(stats["ratio"]["max"]),
                },
            },
            "top_days": [
                {
                    "label": _format_day(record),
                    "single": _mask_count(record.single_female),
                    "female": _mask_count(record.female),
                    "total": _mask_total(record.total),
                    "ratio": _mask_ratio(record.ratio * 100),
                }
                for record in context.top_days
            ],
            "trend": {
                "single": _trend_direction(context.trend.get("single")),
                "female": _trend_direction(context.trend.get("female")),
                "ratio": _trend_direction(context.trend.get("ratio")),
            },
            "weekday_profile": {
                _weekday_label(weekday): {
                    "single": _mask_count(profile["single"]),
                    "female": _mask_count(profile["female"]),
                    "total": _mask_total(profile["total"]),
                    "ratio": _mask_ratio(profile["ratio"]),
                }
                for weekday, profile in sorted(context.weekday_profile.items())
            },
        }
    )
    return masked


def send_slack_message(webhook: str, payload: Dict[str, Any], fallback_text: str) -> None:
    if not webhook:
        LOGGER.warning("SLACK_WEBHOOK_URL is not set; skipping Slack notification")
        LOGGER.info("Fallback summary (no webhook): %s", fallback_text)
        return
    try:
        response = requests.post(webhook, json=payload, timeout=10)
        response.raise_for_status()
        LOGGER.info("Slack notification sent via block kit")
        return
    except Exception as exc:
        LOGGER.error("Slack block send failed: %s", exc)
    try:
        response = requests.post(webhook, json={"text": fallback_text}, timeout=10)
        response.raise_for_status()
        LOGGER.info("Slack fallback text sent")
    except Exception as exc:
        LOGGER.error("Slack fallback also failed: %s", exc)


def send_simple_message(webhook: str, message: str, title: str) -> None:
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}*\n{message}"},
            }
        ],
        "text": f"{title} {message}",
    }
    send_slack_message(webhook, payload, f"{title} {message}")


def handle_no_data(period_title: str, webhook: str, summary_title: str) -> None:
    message = "No data for this period / 集計対象なし"
    title = f"Cheekschecker {period_title}"
    payload, fallback, sections = build_placeholder_summary_payload(title, message)
    append_step_summary(summary_title, sections, fallback)
    send_slack_message(webhook, payload, fallback)


def handle_broken_data(period_title: str, webhook: str, summary_title: str) -> None:
    message = "集計対象なし/壊れた"
    title = f"Cheekschecker {period_title}"
    payload, fallback, sections = build_placeholder_summary_payload(title, message)
    append_step_summary(summary_title, sections, fallback)
    send_slack_message(webhook, payload, fallback)


def run_summary(args: argparse.Namespace) -> int:
    history_meta = _load_masked_history(args.history)
    dataset = load_raw_dataset(args.raw_data)
    period_title = "週次サマリー" if args.period == "weekly" else "月次サマリー"
    webhook = args.slack_webhook
    summary_title = STEP_SUMMARY_TITLES.get(args.period, period_title)

    try:
        context = build_summary_context(args.period, dataset)
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.exception("Failed to build summary context: %s", exc)
        context = None

    store = _load_summary_store(args.output)
    store[args.period] = build_masked_summary(context, history_meta=history_meta)
    _save_summary_store(args.output, store)

    if context is None:
        if dataset.current:
            handle_broken_data(period_title, webhook, summary_title)
        else:
            handle_no_data(period_title, webhook, summary_title)
        return 0

    logical_today = datetime.now(tz=JST).date()
    header_title = f"Cheekschecker {period_title}"
    payload, fallback_text = build_slack_payload(
        context, header_title, logical_today
    )
    summary_sections = payload.pop("_summary_sections", [])
    slack_blocks = payload.pop("_slack_blocks", payload.get("blocks", []))
    payload["blocks"] = slack_blocks
    append_step_summary(summary_title, summary_sections, fallback_text)
    send_slack_message(webhook, payload, fallback_text)
    return 0


def run_ping(args: argparse.Namespace) -> int:
    webhook = args.slack_webhook
    send_simple_message(webhook, "Webhook OK", "Cheekschecker: Webhook OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cheekschecker summary helper")
    parser.add_argument("--period", choices=["weekly", "monthly"], required=False, default="weekly")
    parser.add_argument("--raw-data", type=Path, dest="raw_data")
    parser.add_argument("--history", type=Path, default=Path("history_masked.json"))
    parser.add_argument("--output", type=Path, default=Path("summary_masked.json"))
    parser.add_argument("--ping-only", action="store_true")
    parser.add_argument("--slack-webhook", dest="slack_webhook", default=os.getenv("SLACK_WEBHOOK_URL", ""))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.ping_only:
        return run_ping(args)
    if not args.raw_data:
        LOGGER.warning("--raw-data not provided; treating as no data")
        period_title = "週次サマリー" if args.period == "weekly" else "月次サマリー"
        summary_title = STEP_SUMMARY_TITLES.get(args.period, period_title)
        handle_no_data(period_title, args.slack_webhook, summary_title)
        return 0
    return run_summary(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
