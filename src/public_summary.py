"""Public-safe summary assembly shared by CLI entry points."""

from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from zoneinfo import ZoneInfo

from src.masking import DEFAULT_MASKING_CONFIG, CountBand, MaskingConfig, RatioBand


LOGGER = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")

DOW_JP = ["月", "火", "水", "木", "金", "土", "日"]
DOW_ORDER = [0, 1, 2, 3, 4, 5, 6]
SUMMARY_MODES = {"weekly": 7, "monthly": 30}


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
    window_days: int
    logical_today: Optional[date]
    current: List[DailyRecord]
    previous: List[DailyRecord]
    fetch_status: str = "ok"
    fetch_error: Optional[str] = None


@dataclass(frozen=True)
class CoverageWindow:
    target_days: int
    observed_days: int
    raw_days: int
    masked_days: int
    missing_days: int


@dataclass(frozen=True)
class SummaryRecord:
    business_day: date
    single_value: float
    female_value: float
    total_value: float
    ratio_value: float
    single_label: str
    female_label: str
    total_label: str
    ratio_label: str
    source: str

    @property
    def weekday(self) -> int:
        return self.business_day.weekday()


@dataclass(frozen=True)
class SummaryContext:
    period_key: str
    period_label: str
    period_start: date
    period_end: date
    current: List[SummaryRecord]
    previous: List[SummaryRecord]
    coverage_current: CoverageWindow
    coverage_previous: CoverageWindow
    stats: Dict[str, Dict[str, str]]
    top_days: List[SummaryRecord]
    weekday_profile: Dict[int, Dict[str, str]]
    trend: Dict[str, str]

    @property
    def day_count(self) -> int:
        return len(self.current)


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
    single = max(0, _coerce_int(data.get("single_female")))
    female = max(0, _coerce_int(data.get("female")))
    total = max(0, _coerce_int(data.get("total"), female))
    if total <= 0:
        total = max(female, single)
    ratio = _coerce_float(data.get("ratio"), 0.0)
    if ratio <= 0 and total:
        ratio = female / total
    ratio = max(0.0, min(1.0, ratio))
    return DailyRecord(
        business_day=business_day,
        single_female=single,
        female=female,
        total=total,
        ratio=ratio,
    )


def raw_dataset_from_dict(raw: Dict[str, Any]) -> RawDataset:
    current = [_record_from_dict(item) for item in raw.get("days", []) if isinstance(item, dict)]
    previous = [
        _record_from_dict(item)
        for item in raw.get("previous_days", [])
        if isinstance(item, dict)
    ]
    return RawDataset(
        period_label=str(raw.get("period_label") or "").strip(),
        window_days=max(0, _coerce_int(raw.get("window_days"), 0)),
        logical_today=_parse_iso_date(raw.get("logical_today")),
        current=[item for item in current if item is not None],
        previous=[item for item in previous if item is not None],
        fetch_status=str(raw.get("fetch_status") or "ok"),
        fetch_error=str(raw.get("fetch_error") or "").strip() or None,
    )


def load_raw_dataset(path: Optional[Path]) -> RawDataset:
    if not path or not path.exists():
        LOGGER.warning("Raw dataset is missing: %s", path)
        return RawDataset(period_label="", window_days=0, logical_today=None, current=[], previous=[])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        LOGGER.error("Failed to decode raw dataset %s: %s", path, exc)
        return RawDataset(period_label="", window_days=0, logical_today=None, current=[], previous=[])
    if not isinstance(raw, dict):
        LOGGER.warning("Raw dataset did not contain a JSON object: %s", path)
        return RawDataset(period_label="", window_days=0, logical_today=None, current=[], previous=[])
    return raw_dataset_from_dict(raw)


def _bin_value(value: float, bands: Sequence[Tuple[int, Optional[int], str]]) -> str:
    rounded = int(round(value))
    for low, high, label in bands:
        if high is None and rounded >= low:
            return label
        if high is not None and low <= rounded <= high:
            return label
    return bands[-1][2]


def _bin_ratio(value: float, bands: Sequence[Tuple[float, Optional[float], str]]) -> str:
    normalized = max(0.0, min(1.0, value))
    for low, high, label in bands:
        if high is None and normalized >= low:
            return label
        if high is not None and low <= normalized <= high:
            return label
    return bands[-1][2]


def _mask_labels_for_raw(record: DailyRecord, config: MaskingConfig) -> Dict[str, str]:
    return {
        "single": _bin_value(float(record.single_female), config.count_bands),
        "female": _bin_value(float(record.female), config.count_bands),
        "total": _bin_value(float(record.total), config.total_bands),
        "ratio": _bin_ratio(record.ratio, config.ratio_bands),
    }


def _band_midpoint(low: int | float, high: Optional[int | float]) -> float:
    if high is None:
        if isinstance(low, float):
            return min(1.0, float(low) + 0.05)
        return float(low + max(1, round(low * 0.2)))
    return (float(low) + float(high)) / 2.0


def _label_to_midpoint(
    label: str,
    bands: Sequence[Tuple[int | float, Optional[int | float], str]],
) -> Optional[float]:
    for low, high, candidate in bands:
        if candidate == label:
            return _band_midpoint(low, high)
    return None


def _summary_record_from_raw(record: DailyRecord, config: MaskingConfig) -> SummaryRecord:
    labels = _mask_labels_for_raw(record, config)
    return SummaryRecord(
        business_day=record.business_day,
        single_value=float(record.single_female),
        female_value=float(record.female),
        total_value=float(record.total),
        ratio_value=float(record.ratio),
        single_label=labels["single"],
        female_label=labels["female"],
        total_label=labels["total"],
        ratio_label=labels["ratio"],
        source="raw",
    )


def _summary_record_from_masked(
    target_day: date,
    masked: Dict[str, Any],
    config: MaskingConfig,
) -> Optional[SummaryRecord]:
    single_label = str(masked.get("single") or "")
    female_label = str(masked.get("female") or "")
    total_label = str(masked.get("total") or "")
    ratio_label = str(masked.get("ratio") or "")
    single_value = _label_to_midpoint(single_label, config.count_bands)
    female_value = _label_to_midpoint(female_label, config.count_bands)
    total_value = _label_to_midpoint(total_label, config.total_bands)
    ratio_value = _label_to_midpoint(ratio_label, config.ratio_bands)
    if None in {single_value, female_value, total_value, ratio_value}:
        return None
    return SummaryRecord(
        business_day=target_day,
        single_value=float(single_value),
        female_value=float(female_value),
        total_value=float(total_value),
        ratio_value=float(ratio_value),
        single_label=single_label,
        female_label=female_label,
        total_label=total_label,
        ratio_label=ratio_label,
        source="masked",
    )


def _build_window_records(
    target_days: Sequence[date],
    raw_records: Sequence[DailyRecord],
    history_days: Dict[str, Any],
    config: MaskingConfig,
) -> Tuple[List[SummaryRecord], CoverageWindow]:
    raw_map = {record.business_day: record for record in raw_records}
    summary_records: List[SummaryRecord] = []
    raw_days = 0
    masked_days = 0
    missing_days = 0

    for target_day in target_days:
        raw_record = raw_map.get(target_day)
        if raw_record is not None:
            summary_records.append(_summary_record_from_raw(raw_record, config))
            raw_days += 1
            continue

        masked_value = history_days.get(target_day.isoformat())
        if isinstance(masked_value, dict):
            masked_record = _summary_record_from_masked(target_day, masked_value, config)
            if masked_record is not None:
                summary_records.append(masked_record)
                masked_days += 1
                continue

        missing_days += 1

    return summary_records, CoverageWindow(
        target_days=len(target_days),
        observed_days=len(summary_records),
        raw_days=raw_days,
        masked_days=masked_days,
        missing_days=missing_days,
    )


def _target_days(logical_today: date, window_days: int) -> Tuple[List[date], List[date]]:
    current_start = logical_today - timedelta(days=window_days - 1)
    previous_start = current_start - timedelta(days=window_days)
    previous_end = current_start - timedelta(days=1)
    current_days = [
        current_start + timedelta(days=index)
        for index in range(window_days)
    ]
    previous_days = [
        previous_start + timedelta(days=index)
        for index in range((previous_end - previous_start).days + 1)
    ]
    return current_days, previous_days


def _safe_mean(values: Sequence[float]) -> Optional[float]:
    return statistics.mean(values) if values else None


def _safe_median(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _safe_max(values: Sequence[float]) -> Optional[float]:
    return max(values) if values else None


def _label_stat_bundle(
    records: Sequence[SummaryRecord],
    value_getter: Callable[[SummaryRecord], float],
    band_getter: Callable[[float], str],
) -> Dict[str, str]:
    values = [value_getter(record) for record in records]
    average = _safe_mean(values)
    median = _safe_median(values)
    maximum = _safe_max(values)
    return {
        "average": "-" if average is None else band_getter(average),
        "median": "-" if median is None else band_getter(median),
        "max": "-" if maximum is None else band_getter(maximum),
    }


def _calc_stats(records: Sequence[SummaryRecord], config: MaskingConfig) -> Dict[str, Dict[str, str]]:
    return {
        "single": _label_stat_bundle(
            records,
            lambda record: record.single_value,
            lambda value: _bin_value(value, config.count_bands),
        ),
        "female": _label_stat_bundle(
            records,
            lambda record: record.female_value,
            lambda value: _bin_value(value, config.count_bands),
        ),
        "total": _label_stat_bundle(
            records,
            lambda record: record.total_value,
            lambda value: _bin_value(value, config.total_bands),
        ),
        "ratio": _label_stat_bundle(
            records,
            lambda record: record.ratio_value,
            lambda value: _bin_ratio(value, config.ratio_bands),
        ),
    }


def _calc_top_days(records: Sequence[SummaryRecord], limit: int = 3) -> List[SummaryRecord]:
    ordered = sorted(
        records,
        key=lambda record: (
            record.ratio_value,
            record.single_value,
            record.female_value,
            record.total_value,
            record.business_day.toordinal(),
        ),
        reverse=True,
    )
    return ordered[:limit]


def _calc_weekday_profile(
    records: Sequence[SummaryRecord],
    config: MaskingConfig,
) -> Dict[int, Dict[str, str]]:
    buckets: Dict[int, List[SummaryRecord]] = {}
    for record in records:
        buckets.setdefault(record.weekday, []).append(record)
    profile: Dict[int, Dict[str, str]] = {}
    for weekday, items in buckets.items():
        profile[weekday] = {
            "single": _bin_value(_safe_mean([item.single_value for item in items]) or 0, config.count_bands),
            "female": _bin_value(_safe_mean([item.female_value for item in items]) or 0, config.count_bands),
            "total": _bin_value(_safe_mean([item.total_value for item in items]) or 0, config.total_bands),
            "ratio": _bin_ratio(_safe_mean([item.ratio_value for item in items]) or 0.0, config.ratio_bands),
        }
    return profile


def _trend_direction(current_avg: Optional[float], previous_avg: Optional[float], *, ratio: bool = False) -> str:
    if current_avg is None or previous_avg is None:
        return "unknown"
    threshold = 0.03 if ratio else 0.5
    diff = current_avg - previous_avg
    if diff > threshold:
        return "up"
    if diff < -threshold:
        return "down"
    return "flat"


def _calc_trend(
    current: Sequence[SummaryRecord],
    previous: Sequence[SummaryRecord],
) -> Dict[str, str]:
    current_single = _safe_mean([record.single_value for record in current])
    previous_single = _safe_mean([record.single_value for record in previous])
    current_female = _safe_mean([record.female_value for record in current])
    previous_female = _safe_mean([record.female_value for record in previous])
    current_ratio = _safe_mean([record.ratio_value for record in current])
    previous_ratio = _safe_mean([record.ratio_value for record in previous])
    return {
        "single": _trend_direction(current_single, previous_single),
        "female": _trend_direction(current_female, previous_female),
        "ratio": _trend_direction(current_ratio, previous_ratio, ratio=True),
    }


def build_summary_context(
    period_key: str,
    dataset: RawDataset,
    history_meta: Dict[str, Any],
    *,
    masking_config: MaskingConfig = DEFAULT_MASKING_CONFIG,
) -> Optional[SummaryContext]:
    window_days = dataset.window_days or SUMMARY_MODES.get(period_key, 7)
    raw_days = dataset.current
    logical_today = dataset.logical_today
    if logical_today is None and raw_days:
        logical_today = max(record.business_day for record in raw_days)
    if logical_today is None:
        return None

    current_days, previous_days = _target_days(logical_today, window_days)
    history_days = history_meta.get("days", {})
    if not isinstance(history_days, dict):
        history_days = {}

    current_records, coverage_current = _build_window_records(
        current_days, dataset.current, history_days, masking_config
    )
    previous_records, coverage_previous = _build_window_records(
        previous_days, dataset.previous, history_days, masking_config
    )
    if not current_records:
        return None

    stats = _calc_stats(current_records, masking_config)
    top_days = _calc_top_days(current_records)
    weekday_profile = _calc_weekday_profile(current_records, masking_config)
    trend = _calc_trend(current_records, previous_records)
    period_label = dataset.period_label or ("過去7日" if window_days == 7 else "過去30日")

    return SummaryContext(
        period_key=period_key,
        period_label=period_label,
        period_start=current_days[0],
        period_end=current_days[-1],
        current=current_records,
        previous=previous_records,
        coverage_current=coverage_current,
        coverage_previous=coverage_previous,
        stats=stats,
        top_days=top_days,
        weekday_profile=weekday_profile,
        trend=trend,
    )


def _weekday_label(weekday: int) -> str:
    if 0 <= weekday < len(DOW_JP):
        return DOW_JP[weekday]
    return "?"


def _format_date_range(start: date, end: date) -> str:
    start_label = f"{start.month:02d}/{start.day:02d}({_weekday_label(start.weekday())})"
    end_label = f"{end.month:02d}/{end.day:02d}({_weekday_label(end.weekday())})"
    return f"{start_label}〜{end_label}"


def _format_latest_field(latest: SummaryRecord) -> List[str]:
    latest_label = f"{latest.business_day.month:02d}/{latest.business_day.day:02d}({_weekday_label(latest.weekday)})"
    source_label = "現run" if latest.source == "raw" else "masked補完"
    return [
        latest_label,
        f"単女{latest.single_label} 女{latest.female_label}/全{latest.total_label}",
        f"比率{latest.ratio_label} ({source_label})",
    ]


def _format_representation_field(stats: Dict[str, Dict[str, str]]) -> List[str]:
    return [
        f"平均帯域 単女{stats['single']['average']} 女{stats['female']['average']} 比率{stats['ratio']['average']}",
        f"中央値帯域 単女{stats['single']['median']} 女{stats['female']['median']} 比率{stats['ratio']['median']}",
        f"最大帯域 単女{stats['single']['max']} 女{stats['female']['max']} 比率{stats['ratio']['max']}",
    ]


def _format_day(record: SummaryRecord) -> str:
    return f"{record.business_day.day}日({_weekday_label(record.weekday)})"


def _format_top_days_section(top_days: Sequence[SummaryRecord]) -> List[str]:
    if not top_days:
        return ["• 該当なし"]
    return [
        f"• {_format_day(record)} 単女{record.single_label} 女{record.female_label}/全{record.total_label} ({record.ratio_label})"
        for record in top_days
    ]


def _format_trend_value(direction: str) -> str:
    return {
        "up": "↗",
        "down": "↘",
        "flat": "→",
        "unknown": "?"
    }.get(direction, "?")


def _format_weekday_profile_line(weekday_profile: Dict[int, Dict[str, str]]) -> str:
    parts: List[str] = []
    for weekday in DOW_ORDER:
        if weekday not in weekday_profile:
            continue
        profile = weekday_profile[weekday]
        parts.append(
            f"{_weekday_label(weekday)} 単{profile['single']} 女{profile['female']} ({profile['ratio']})"
        )
    return "• 曜日: " + (" / ".join(parts) if parts else "データ不足")


def _coverage_line(current: CoverageWindow, previous: CoverageWindow) -> str:
    return (
        "• カバレッジ: "
        f"current raw {current.raw_days}/{current.target_days}, "
        f"masked {current.masked_days}, missing {current.missing_days}; "
        f"previous raw {previous.raw_days}/{previous.target_days}, "
        f"masked {previous.masked_days}, missing {previous.missing_days}"
    )


def _build_summary_actions_block() -> Dict[str, Any]:
    repo = os.getenv("GITHUB_REPOSITORY", "wadansyaku/cheekschecker")
    branch = os.getenv("GITHUB_REF_NAME", "main")
    base_url = f"https://raw.githubusercontent.com/{repo}/{branch}"
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
    context: SummaryContext,
    title: str,
    *,
    logical_today: Optional[date] = None,
) -> Tuple[Dict[str, Any], str, List[Tuple[str, List[str]]]]:
    logical_ref = logical_today or context.period_end
    today_entry = next((record for record in context.current if record.business_day == logical_ref), None)
    latest = today_entry or max(context.current, key=lambda record: record.business_day)
    latest_lines = _format_latest_field(latest)
    stats_lines = _format_representation_field(context.stats)
    top_lines = _format_top_days_section(context.top_days)
    trend_line = (
        "• 傾向: "
        f"単女{_format_trend_value(context.trend['single'])} / "
        f"女{_format_trend_value(context.trend['female'])} / "
        f"比率{_format_trend_value(context.trend['ratio'])}"
    )
    weekday_line = _format_weekday_profile_line(context.weekday_profile)
    coverage_line = _coverage_line(context.coverage_current, context.coverage_previous)
    full_title = title if title.startswith("Cheekschecker") else f"Cheekschecker {title}"
    context_elements = [
        {"type": "mrkdwn", "text": f"対象期間: {_format_date_range(context.period_start, context.period_end)}"},
        {"type": "mrkdwn", "text": "mode: public-safe approximation"},
        {"type": "mrkdwn", "text": coverage_line.removeprefix("• ")},
    ]

    slack_blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": full_title, "emoji": False}},
        {"type": "context", "elements": context_elements},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*最新観測*\n" + "\n".join(latest_lines)},
                {"type": "mrkdwn", "text": "*期間帯域*\n" + "\n".join(stats_lines)},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Top3 好条件日*\n" + "\n".join(top_lines)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": trend_line}},
        {"type": "section", "text": {"type": "mrkdwn", "text": weekday_line}},
        {"type": "section", "text": {"type": "mrkdwn", "text": coverage_line}},
        _build_summary_actions_block(),
    ]

    fallback_lines = [
        full_title,
        f"期間: {_format_date_range(context.period_start, context.period_end)}",
        "mode: public-safe approximation",
        "",
        "【最新観測】",
        *latest_lines,
        "",
        "【期間帯域】",
        *stats_lines,
        "",
        "【Top3】",
        *top_lines,
        "",
        trend_line,
        weekday_line,
        coverage_line,
    ]
    fallback_text = "\n".join(fallback_lines)
    step_sections = [
        ("最新観測", latest_lines),
        ("期間帯域", stats_lines),
        ("Top3", top_lines),
        ("傾向・曜日・カバレッジ", [trend_line, weekday_line, coverage_line]),
    ]
    return {"text": fallback_text, "blocks": slack_blocks}, fallback_text, step_sections


def build_placeholder_summary_payload(
    title: str,
    message: str,
) -> Tuple[Dict[str, Any], str, List[Tuple[str, List[str]]]]:
    fallback = f"{title} {message}".strip()
    sections = [
        ("最新観測", ["該当なし"]),
        ("期間帯域", ["該当なし"]),
        ("Top3/傾向/カバレッジ", [message]),
    ]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": False}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "mode: public-safe approximation"},
                {"type": "mrkdwn", "text": message},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*最新観測*\n該当なし"},
                {"type": "mrkdwn", "text": "*期間帯域*\n該当なし"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Top3 / 傾向 / カバレッジ*\n" + message}},
        _build_summary_actions_block(),
    ]
    return {"text": fallback, "blocks": blocks}, fallback, sections


def build_masked_summary(
    context: Optional[SummaryContext],
    *,
    history_meta: Dict[str, Any],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "mask_level": history_meta.get("mask_level", 1),
        "mode": "public-safe",
    }
    if context is None:
        payload.update(
            {
                "status": "no-data",
                "coverage": {
                    "current": {
                        "target_days": 0,
                        "observed_days": 0,
                        "raw_days": 0,
                        "masked_days": 0,
                        "missing_days": 0,
                    },
                    "previous": {
                        "target_days": 0,
                        "observed_days": 0,
                        "raw_days": 0,
                        "masked_days": 0,
                        "missing_days": 0,
                    },
                },
            }
        )
        return payload

    payload.update(
        {
            "status": "ok",
            "period_start": context.period_start.isoformat(),
            "period_end": context.period_end.isoformat(),
            "day_count": context.day_count,
            "stats": context.stats,
            "top_days": [
                {
                    "label": _format_day(record),
                    "single": record.single_label,
                    "female": record.female_label,
                    "total": record.total_label,
                    "ratio": record.ratio_label,
                    "source": record.source,
                }
                for record in context.top_days
            ],
            "trend": context.trend,
            "weekday_profile": {
                _weekday_label(weekday): profile
                for weekday, profile in sorted(context.weekday_profile.items())
            },
            "coverage": {
                "current": {
                    "target_days": context.coverage_current.target_days,
                    "observed_days": context.coverage_current.observed_days,
                    "raw_days": context.coverage_current.raw_days,
                    "masked_days": context.coverage_current.masked_days,
                    "missing_days": context.coverage_current.missing_days,
                },
                "previous": {
                    "target_days": context.coverage_previous.target_days,
                    "observed_days": context.coverage_previous.observed_days,
                    "raw_days": context.coverage_previous.raw_days,
                    "masked_days": context.coverage_previous.masked_days,
                    "missing_days": context.coverage_previous.missing_days,
                },
            },
        }
    )
    return payload


__all__ = [
    "CoverageWindow",
    "DailyRecord",
    "JST",
    "RawDataset",
    "SummaryContext",
    "build_masked_summary",
    "build_placeholder_summary_payload",
    "build_slack_payload",
    "build_summary_context",
    "load_raw_dataset",
    "raw_dataset_from_dict",
]
