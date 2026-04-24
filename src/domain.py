"""Shared domain types for Cheekschecker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
DOW_EN = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
DOW_JP = {"Sun": "日", "Mon": "月", "Tue": "火", "Wed": "水", "Thu": "木", "Fri": "金", "Sat": "土"}


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


__all__ = [
    "DOW_EN",
    "DOW_JP",
    "DailyEntry",
    "JST",
]
