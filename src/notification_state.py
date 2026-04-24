"""Notification stage transition rules."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.domain import DailyEntry


VALID_STAGES = {"none", "initial", "bonus"}


def coerce_stage(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in VALID_STAGES:
            return lowered
    return "none"


def coerce_last_notified(value: Any) -> Optional[int]:
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

    prev_stage = coerce_stage(prev_state.get("stage")) if prev_state else "none"
    prev_last = coerce_last_notified(prev_state.get("last_notified_at")) if prev_state else None

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


__all__ = [
    "coerce_last_notified",
    "coerce_stage",
    "evaluate_stage_transition",
]
