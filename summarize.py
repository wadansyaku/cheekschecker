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

LOGGER = logging.getLogger("summarize")
JST = ZoneInfo("Asia/Tokyo")

DOW_JP = ["月", "火", "水", "木", "金", "土", "日"]
DOW_ORDER = [0, 1, 2, 3, 4, 5, 6]

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
            -r.business_day.toordinal(),
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


def build_slack_payload(context: SummaryContext, title: str) -> Tuple[Dict[str, Any], str]:
    def _value_or_zero(value: Optional[float]) -> float:
        return float(value) if value is not None else 0.0

    def _int_or_zero(value: Optional[float]) -> int:
        return int(round(value)) if value is not None else 0

    range_text = f"{context.period_start.month:02d}/{context.period_start.day:02d}({_weekday_label(context.period_start.weekday())})"
    range_text += "〜"
    range_text += f"{context.period_end.month:02d}/{context.period_end.day:02d}({_weekday_label(context.period_end.weekday())})"
    headline = f"*対象期間*: {range_text}\n*対象営業日*: {context.day_count}日"

    stats_single = context.stats["single"]
    stats_female = context.stats["female"]
    stats_ratio = context.stats["ratio"]

    fields = [
        {
            "type": "mrkdwn",
            "text": (
                "*単独女性*\n"
                f"平均 {_value_or_zero(stats_single['average']):.1f} / 中央 {_value_or_zero(stats_single['median']):.1f} / 最大 {_int_or_zero(stats_single['max'])}"
            ),
        },
        {
            "type": "mrkdwn",
            "text": (
                "*女性総数*\n"
                f"平均 {_value_or_zero(stats_female['average']):.1f} / 中央 {_value_or_zero(stats_female['median']):.1f} / 最大 {_int_or_zero(stats_female['max'])}"
            ),
        },
        {
            "type": "mrkdwn",
            "text": (
                "*女性比率*\n"
                f"平均 {_value_or_zero(stats_ratio['average']):.1f}% / 中央 {_value_or_zero(stats_ratio['median']):.1f}% / 最大 {_value_or_zero(stats_ratio['max']):.1f}%"
            ),
        },
    ]

    hot_lines = [
        f"• {_format_day(record)} 単女{record.single_female} 女{record.female}/全{record.total} ({_format_ratio_percent(record.ratio * 100)})"
        for record in context.top_days
    ]
    hot_text = "\n".join(hot_lines) if hot_lines else "該当なし"

    trend_lines = [
        f"• 単独女性: {_format_trend_value('single', context.trend.get('single'))}",
        f"• 女性総数: {_format_trend_value('female', context.trend.get('female'))}",
        f"• 女性比率: {_format_trend_value('ratio', context.trend.get('ratio'))}",
    ]

    weekday_lines = []
    for weekday in DOW_ORDER:
        if weekday not in context.weekday_profile:
            continue
        profile = context.weekday_profile[weekday]
        weekday_lines.append(
            f"• {_weekday_label(weekday)}: 単{_value_or_zero(profile['single']):.1f} 女{_value_or_zero(profile['female']):.1f}/全{_value_or_zero(profile['total']):.1f} ({_value_or_zero(profile['ratio']):.1f}%)"
        )
    if not weekday_lines:
        weekday_lines.append("• データ不足")

    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Cheekschecker {title}", "emoji": False}},
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "section", "fields": fields},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Hot day Top3*\n" + hot_text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*傾向 (直前比)*\n" + "\n".join(trend_lines)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*曜日別プロファイル*\n" + "\n".join(weekday_lines)}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"更新: {datetime.now(tz=JST).strftime('%m/%d %H:%M')} JST"},
            ],
        },
    ]

    fallback_lines = [
        f"Cheekschecker {title}",
        headline.replace("*", ""),
        "単独女性 平均 {0:.1f} / 中央 {1:.1f} / 最大 {2}".format(
            _value_or_zero(stats_single["average"]),
            _value_or_zero(stats_single["median"]),
            _int_or_zero(stats_single["max"]),
        ),
        "女性総数 平均 {0:.1f} / 中央 {1:.1f} / 最大 {2}".format(
            _value_or_zero(stats_female["average"]),
            _value_or_zero(stats_female["median"]),
            _int_or_zero(stats_female["max"]),
        ),
        "女性比率 平均 {0:.1f}% / 中央 {1:.1f}% / 最大 {2:.1f}%".format(
            _value_or_zero(stats_ratio["average"]),
            _value_or_zero(stats_ratio["median"]),
            _value_or_zero(stats_ratio["max"]),
        ),
    ]
    if hot_lines:
        fallback_lines.append("Hot day Top3:")
        fallback_lines.extend(line.replace("• ", "- ") for line in hot_lines)
    fallback_lines.append("傾向:")
    fallback_lines.extend(line.replace("• ", "- ") for line in trend_lines)
    fallback_lines.append("曜日別プロファイル:")
    fallback_lines.extend(line.replace("• ", "- ") for line in weekday_lines)
    fallback_text = "\n".join(fallback_lines)
    payload = {"text": fallback_text, "blocks": blocks}
    return payload, fallback_text


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


def handle_no_data(period_title: str, webhook: str) -> None:
    message = "No data for this period / 集計対象なし"
    send_simple_message(webhook, message, f"Cheekschecker {period_title}")


def handle_broken_data(period_title: str, webhook: str) -> None:
    message = "集計対象なし/壊れた"
    send_simple_message(webhook, message, f"Cheekschecker {period_title}")


def run_summary(args: argparse.Namespace) -> int:
    history_meta = _load_masked_history(args.history)
    dataset = load_raw_dataset(args.raw_data)
    period_title = "週次サマリー" if args.period == "weekly" else "月次サマリー"
    webhook = args.slack_webhook

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
            handle_broken_data(period_title, webhook)
        else:
            handle_no_data(period_title, webhook)
        return 0

    payload, fallback_text = build_slack_payload(context, period_title)
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
        handle_no_data(
            "週次サマリー" if args.period == "weekly" else "月次サマリー",
            args.slack_webhook,
        )
        return 0
    return run_summary(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
