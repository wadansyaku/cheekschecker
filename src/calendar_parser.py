"""Calendar HTML parsing and date inference."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag

from src.domain import DOW_EN, DOW_JP, DailyEntry, JST


LOGGER = logging.getLogger(__name__)

DATE_ATTR_CANDIDATES = (
    "data-date",
    "data-day",
    "data-date-iso",
    "data-day-iso",
    "data-full-date",
)
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
YEAR_MONTH_PATTERNS = (
    re.compile(r"(?P<year>[12][0-9]{3})\s*年\s*(?P<month>0?[1-9]|1[0-2])\s*月"),
    re.compile(r"(?P<year>[12][0-9]{3})\s*[/-]\s*(?P<month>0?[1-9]|1[0-2])(?:\D|$)"),
)
MONTH_ONLY_PATTERN = re.compile(r"(?<!\d)(?P<month>0?[1-9]|1[0-2])\s*月")
YEAR_MONTH_HEADING_PATTERNS = (
    re.compile(r"^[12][0-9]{3}\s*年\s*(0?[1-9]|1[0-2])\s*月\s*$"),
    re.compile(r"^[12][0-9]{3}\s*[/-]\s*(0?[1-9]|1[0-2])\s*$"),
)
MONTH_ONLY_HEADING_PATTERN = re.compile(r"^(0?[1-9]|1[0-2])\s*月$")
WEEKDAY_JP_TO_EN = {label: key for key, label in DOW_JP.items()}


class CalendarParseSettings(Protocol):
    @property
    def female_min(self) -> int: ...

    @property
    def female_ratio_min(self) -> float: ...

    @property
    def min_total(self) -> Optional[int]: ...

    @property
    def exclude_keywords(self) -> Sequence[str]: ...

    @property
    def include_dow(self) -> Sequence[str]: ...


def business_dow_label(dt: date) -> str:
    return DOW_EN[(dt.weekday() + 1) % 7]


def should_exclude_text(text: str, keywords: Sequence[str]) -> bool:
    lowered = text.lower()
    for keyword in keywords:
        if not keyword:
            continue
        if "スタッフ" in keyword or "staff" in keyword:
            continue
        if keyword in lowered:
            return True
    return False


def extract_numeric_counts(text: str) -> List[int]:
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


def count_participant_line(text: str) -> Tuple[int, int, int]:
    male_count = text.count("♂")
    female_symbols = text.count("♀")
    numbers = extract_numeric_counts(text)

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

    last_day_current = calendar.monthrange(year, month)[1]
    current_month_date = date(year, month, min(day, last_day_current))
    days_diff = (current_month_date - reference_date).days

    if days_diff < -15:
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        last_day_next = calendar.monthrange(next_year, next_month)[1]
        return date(next_year, next_month, min(day, last_day_next))

    if days_diff > 20:
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        last_day_prev = calendar.monthrange(prev_year, prev_month)[1]
        return date(prev_year, prev_month, min(day, last_day_prev))

    return current_month_date


def _normalise_digits(text: str) -> str:
    return text.translate(FULLWIDTH_TO_ASCII)


def _closest_year_for_month(month: int, reference_date: date) -> int:
    candidates = [reference_date.year - 1, reference_date.year, reference_date.year + 1]
    return min(
        candidates,
        key=lambda year: abs((date(year, month, 1) - reference_date).days),
    )


def _month_anchor_from_text(text: str, reference_date: date) -> Optional[date]:
    cleaned = _normalise_digits(text).strip()
    if not cleaned:
        return None

    for pattern in YEAR_MONTH_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        year = int(match.group("year"))
        month = int(match.group("month"))
        try:
            return date(year, month, 1)
        except ValueError:
            return None

    match = MONTH_ONLY_PATTERN.search(cleaned)
    if match and len(cleaned) <= 24:
        month = int(match.group("month"))
        year = _closest_year_for_month(month, reference_date)
        return date(year, month, 1)

    return None


def _looks_like_month_heading(text: str) -> bool:
    cleaned = _normalise_digits(text).strip()
    if not cleaned:
        return False
    if any(pattern.fullmatch(cleaned) for pattern in YEAR_MONTH_HEADING_PATTERNS):
        return True
    return bool(MONTH_ONLY_HEADING_PATTERN.fullmatch(cleaned))


def extract_table_month_anchor(table: Tag, reference_date: date) -> Optional[date]:
    for element in table.find_all(["caption", "th"]):
        for text in element.stripped_strings:
            anchor = _month_anchor_from_text(text, reference_date)
            if anchor is not None:
                return anchor

    for row in table.find_all("tr")[:3]:
        for cell in row.find_all(["td", "th"], recursive=False):
            raw_colspan = cell.get("colspan")
            try:
                colspan = int(str(raw_colspan)) if raw_colspan is not None else 1
            except ValueError:
                colspan = 1
            if colspan <= 1:
                continue
            for text in cell.stripped_strings:
                anchor = _month_anchor_from_text(text, reference_date)
                if anchor is not None:
                    return anchor
    return None


def _shift_month(anchor: date, offset: int) -> Tuple[int, int]:
    month_index = (anchor.year * 12) + (anchor.month - 1) + offset
    return month_index // 12, (month_index % 12) + 1


def _date_if_valid(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def infer_entry_date_with_month_anchor(
    day: int,
    reference_date: date,
    month_anchor: date,
    *,
    weekday_hint: Optional[str] = None,
) -> date:
    if day < 1:
        day = 1

    candidates: List[date] = []
    for offset in (-1, 0, 1):
        year, month = _shift_month(month_anchor, offset)
        candidate = _date_if_valid(year, month, day)
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        return infer_entry_date(day, reference_date)

    base_candidate = _date_if_valid(month_anchor.year, month_anchor.month, day)
    if weekday_hint:
        if base_candidate is not None and business_dow_label(base_candidate) == weekday_hint:
            return base_candidate
        weekday_matches = [
            candidate
            for candidate in candidates
            if business_dow_label(candidate) == weekday_hint
        ]
        if weekday_matches:
            candidates = weekday_matches
    elif base_candidate is not None:
        return base_candidate

    return min(
        candidates,
        key=lambda candidate: (
            abs((candidate - reference_date).days),
            0 if (
                candidate.year == month_anchor.year
                and candidate.month == month_anchor.month
            ) else 1,
        ),
    )


def extract_calendar_table(html: str) -> Optional[Tag]:
    soup = BeautifulSoup(html, "lxml")

    table = soup.find("table", attrs={"border": "2"})
    if table and isinstance(table, Tag):
        return table

    dow_tokens = set(DOW_JP.values()) | set(DOW_EN)
    for candidate in soup.find_all("table"):
        if not isinstance(candidate, Tag):
            continue
        strings = list(candidate.stripped_strings)
        if not strings:
            continue

        day_hits = 0
        dow_hits = 0
        for text in strings:
            stripped = text.strip()
            if not stripped:
                continue
            if stripped in dow_tokens:
                dow_hits += 1
            else:
                try:
                    value = int(stripped)
                except ValueError:
                    continue
                if 1 <= value <= 31:
                    day_hits += 1

        if day_hits >= 10 and dow_hits >= 3:
            return candidate

    LOGGER.warning("Calendar table not found")
    return None


def extract_day_number(parts: List[str]) -> Optional[int]:
    for part in parts:
        if _looks_like_month_heading(part):
            continue
        match = re.search(r"(\d{1,2})", part)
        if match:
            return int(match.group(1))
    return None


def extract_weekday_hint(parts: List[str]) -> Optional[str]:
    for part in parts:
        stripped = part.strip()
        lowered = stripped.lower()
        for dow in DOW_EN:
            if lowered == dow.lower():
                return dow
        if stripped in WEEKDAY_JP_TO_EN:
            return WEEKDAY_JP_TO_EN[stripped]
    return None


def parse_date_attribute(raw: str) -> Optional[date]:
    cleaned = raw.strip()
    if not cleaned:
        return None

    iso_candidate = cleaned.split()[0]
    if "T" in iso_candidate:
        iso_candidate = iso_candidate.split("T", 1)[0]
    iso_candidate = iso_candidate.replace("/", "-")

    try:
        return datetime.fromisoformat(iso_candidate).date()
    except ValueError:
        match = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", cleaned)
        if match:
            year, month, day = map(int, match.groups())
            return date(year, month, day)
    return None


def extract_explicit_cell_date(cell: Tag) -> Optional[date]:
    for element in [cell, *cell.find_all(True)]:
        for attr in DATE_ATTR_CANDIDATES:
            raw = element.get(attr)
            if not raw:
                continue
            parsed = parse_date_attribute(str(raw))
            if parsed:
                return parsed
    return None


def extract_content_lines(parts: List[str]) -> List[str]:
    content_lines: List[str] = []
    for part in parts:
        if _looks_like_month_heading(part):
            continue
        if re.fullmatch(r"\d{1,2}", part):
            continue
        if part.lower() in {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}:
            continue
        if part in WEEKDAY_JP_TO_EN:
            continue
        content_lines.append(part)
    return content_lines


def normalise_participant_label(text: str) -> str:
    translated = text.translate(FULLWIDTH_TO_ASCII)
    base = re.sub(r"[♂♀]", "", translated).replace("　", " ").strip()
    if not base:
        return ""

    token = re.split(r"[\s(（\[\{／/|｜・]+", base, maxsplit=1)[0]
    token = token or base

    normalised = token.lower()
    normalised = re.sub(r"[×x＊*]+\s*[0-9]+$", "", normalised)
    normalised = normalised.replace("人", "").replace("名", "").replace("組", "")
    normalised = re.sub(r"[()（）\[\]{}<>『』「」【】、。,.!！?？:：;；~〜'\"`´^＾\\-]", "", normalised)
    normalised = normalised.strip()
    if normalised:
        return normalised

    fallback = re.sub(r"[()（）\[\]{}<>『』「」【】、。,.!！?？:：;；~〜'\"`´^＾\\|／/・-]", "", base.lower())
    fallback = re.sub(r"[0-9]+", "", fallback)
    fallback = fallback.replace("人", "").replace("名", "").replace("組", "")
    fallback = fallback.strip()
    return fallback or base.lower() or base


def count_participants(
    content_lines: List[str], exclude_keywords: Sequence[str]
) -> Dict[str, int]:
    participants: Dict[str, Dict[str, int]] = {}

    for line in content_lines:
        if should_exclude_text(line, exclude_keywords):
            continue
        male, female, single = count_participant_line(line)

        key = normalise_participant_label(line)
        stored = participants.get(key)
        if stored is None:
            participants[key] = {
                "male": male,
                "female": female,
                "single_female": single,
            }
        else:
            stored["male"] = max(stored["male"], male)
            stored["female"] = max(stored["female"], female)
            stored["single_female"] = max(stored["single_female"], single)

    male_total = sum(values["male"] for values in participants.values())
    female_total = sum(values["female"] for values in participants.values())
    single_total = sum(
        1 for values in participants.values() if values["female"] == 1 and values["male"] == 0
    )

    total = male_total + female_total
    return {
        "male": male_total,
        "female": female_total,
        "single_female": single_total,
        "total": total,
    }


def build_daily_entry(
    cell_date: date,
    day_of_month: int,
    counts: Dict[str, int],
    settings: CalendarParseSettings,
) -> DailyEntry:
    business_dow = business_dow_label(cell_date)
    total = counts["total"]
    ratio = (counts["female"] / total) if total else 0.0

    considered = True
    if settings.include_dow and business_dow not in settings.include_dow:
        considered = False
    if settings.min_total is not None and total < settings.min_total:
        considered = False

    required_single = 5 if business_dow in {"Fri", "Sat"} else 3
    female_required = max(settings.female_min, required_single)
    ratio_threshold = max(0.40, settings.female_ratio_min)
    meets = (
        considered
        and counts["single_female"] >= required_single
        and counts["female"] >= female_required
        and ratio >= ratio_threshold
        and total > 0
    )

    return DailyEntry(
        raw_date=cell_date,
        business_day=cell_date,
        day_of_month=day_of_month,
        dow_en=business_dow,
        male=counts["male"],
        female=counts["female"],
        single_female=counts["single_female"],
        total=total,
        ratio=round(ratio, 4),
        considered=considered,
        meets=meets,
        required_single=required_single,
    )


def parse_day_entries(
    html: str,
    *,
    settings: CalendarParseSettings,
    reference_date: Optional[date] = None,
) -> List[DailyEntry]:
    table = extract_calendar_table(html)
    if not table:
        return []

    today = reference_date or datetime.now(tz=JST).date()
    month_anchor = extract_table_month_anchor(table, today)
    results: List[DailyEntry] = []

    for cell in iter_calendar_cells(table):
        entry = parse_calendar_cell(cell, today, settings, month_anchor=month_anchor)
        if entry is None:
            continue

        LOGGER.debug("Parsed entry: %s", entry)
        results.append(entry)

    results.sort(key=lambda e: e.business_day)
    LOGGER.info(
        "parsing_completed entry_count=%d meets_criteria_count=%d",
        len(results),
        sum(1 for e in results if e.meets),
    )
    return results


def iter_calendar_cells(table: Tag) -> Iterable[Tag]:
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        for cell in cells:
            if isinstance(cell, Tag):
                yield cell


def parse_calendar_cell(
    cell: Tag,
    today: date,
    settings: CalendarParseSettings,
    *,
    month_anchor: Optional[date] = None,
) -> Optional[DailyEntry]:
    parts = extract_cell_parts(cell)
    if not parts:
        return None

    explicit_date = extract_explicit_cell_date(cell)
    day_of_month = extract_day_number(parts)
    weekday_hint = extract_weekday_hint(parts)
    resolved_date, resolved_day = resolve_cell_date(
        explicit_date,
        day_of_month,
        today,
        month_anchor=month_anchor,
        weekday_hint=weekday_hint,
    )
    if resolved_date is None or resolved_day is None:
        return None

    content_lines = extract_content_lines(parts)
    counts = count_participants(content_lines, settings.exclude_keywords)
    return build_daily_entry(resolved_date, resolved_day, counts, settings)


def extract_cell_parts(cell: Tag) -> List[str]:
    return [part.strip() for part in cell.stripped_strings if part.strip()]


def resolve_cell_date(
    explicit_date: Optional[date],
    day_of_month: Optional[int],
    today: date,
    *,
    month_anchor: Optional[date] = None,
    weekday_hint: Optional[str] = None,
) -> Tuple[Optional[date], Optional[int]]:
    if explicit_date is None and day_of_month is None:
        return None, None

    if explicit_date is not None:
        resolved_day = day_of_month or explicit_date.day
        return explicit_date, resolved_day

    if day_of_month is None:
        return None, None

    if month_anchor is not None:
        inferred = infer_entry_date_with_month_anchor(
            day_of_month,
            today,
            month_anchor,
            weekday_hint=weekday_hint,
        )
    else:
        inferred = infer_entry_date(day_of_month, today)
    return inferred, day_of_month


__all__ = [
    "CalendarParseSettings",
    "business_dow_label",
    "build_daily_entry",
    "count_participant_line",
    "count_participants",
    "extract_calendar_table",
    "extract_table_month_anchor",
    "infer_entry_date",
    "infer_entry_date_with_month_anchor",
    "parse_day_entries",
]
