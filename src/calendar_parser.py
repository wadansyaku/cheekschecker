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
        match = re.search(r"(\d{1,2})", part)
        if match:
            return int(match.group(1))
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
        if re.fullmatch(r"\d{1,2}", part):
            continue
        if part.lower() in {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}:
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
    results: List[DailyEntry] = []

    for cell in iter_calendar_cells(table):
        entry = parse_calendar_cell(cell, today, settings)
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
) -> Optional[DailyEntry]:
    parts = extract_cell_parts(cell)
    if not parts:
        return None

    explicit_date = extract_explicit_cell_date(cell)
    day_of_month = extract_day_number(parts)
    resolved_date, resolved_day = resolve_cell_date(explicit_date, day_of_month, today)
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
) -> Tuple[Optional[date], Optional[int]]:
    if explicit_date is None and day_of_month is None:
        return None, None

    if explicit_date is not None:
        resolved_day = day_of_month or explicit_date.day
        return explicit_date, resolved_day

    if day_of_month is None:
        return None, None

    inferred = infer_entry_date(day_of_month, today)
    return inferred, day_of_month


__all__ = [
    "CalendarParseSettings",
    "business_dow_label",
    "build_daily_entry",
    "count_participant_line",
    "count_participants",
    "extract_calendar_table",
    "infer_entry_date",
    "parse_day_entries",
]
