"""Masking configuration helpers shared across monitoring and summary jobs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple, TypeVar


LOGGER = logging.getLogger(__name__)


TBandValue = TypeVar("TBandValue", int, float)

Band = Tuple[int | float, Optional[int | float], str]
CountBand = Tuple[int, Optional[int], str]
RatioBand = Tuple[float, Optional[float], str]


@dataclass(frozen=True)
class MaskingConfig:
    """Configuration container describing masking behaviour."""

    count_bands: Tuple[CountBand, ...]
    total_bands: Tuple[CountBand, ...]
    ratio_bands: Tuple[RatioBand, ...]
    level2_words: dict[str, Tuple[str, ...]]
    level2_divisors: dict[str, int]
    level2_ratio_thresholds: Tuple[float, ...]


def _parse_band_sequence(
    raw: Iterable[Any], *, value_cast: Callable[[Any], TBandValue]
) -> Tuple[Tuple[TBandValue, Optional[TBandValue], str], ...]:
    bands: list[Tuple[TBandValue, Optional[TBandValue], str]] = []
    for item in raw:
        if isinstance(item, dict):
            low = item.get("low")
            high = item.get("high")
            label = item.get("label")
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            low, high, label = item
        else:
            LOGGER.warning("Ignoring malformed mask band entry: %s", item)
            continue
        try:
            low_cast = value_cast(low)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid lower bound in mask band: %s", item)
            continue
        if high is None:
            high_cast = None
        else:
            try:
                high_cast = value_cast(high)
            except (TypeError, ValueError):
                LOGGER.warning("Invalid upper bound in mask band: %s", item)
                continue
        label_str = str(label) if label is not None else ""
        bands.append((low_cast, high_cast, label_str))
    return tuple(bands)


def _parse_level2_words(raw: Mapping[str, Sequence[Any]]) -> dict[str, Tuple[str, ...]]:
    parsed: dict[str, Tuple[str, ...]] = {}
    for key, words in raw.items():
        parsed[key] = tuple(str(word) for word in words if str(word))
    return parsed


def _parse_level2_divisors(raw: Mapping[str, Any]) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for key, value in raw.items():
        try:
            parsed[key] = max(1, int(value))
        except (TypeError, ValueError):
            LOGGER.warning("Invalid divisor for %s in masking config: %s", key, value)
    return parsed


def _parse_ratio_thresholds(raw: Sequence[Any]) -> Tuple[float, ...]:
    thresholds: list[float] = []
    for value in raw:
        try:
            thresholds.append(float(value))
        except (TypeError, ValueError):
            LOGGER.warning("Invalid ratio threshold in masking config: %s", value)
    return tuple(sorted(thresholds))


def load_masking_config(path: Optional[str]) -> "MaskingConfig":
    """Load masking configuration from JSON, falling back to defaults."""

    if not path:
        return DEFAULT_MASKING_CONFIG
    config_path = Path(path).expanduser()
    if not config_path.exists():
        LOGGER.warning("Masking config path does not exist: %s", config_path)
        return DEFAULT_MASKING_CONFIG
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        LOGGER.warning("Failed to parse masking config %s: %s", config_path, exc)
        return DEFAULT_MASKING_CONFIG

    count_bands = _parse_band_sequence(raw.get("count_bands", []), value_cast=int)
    total_bands = _parse_band_sequence(raw.get("total_bands", []), value_cast=int)
    ratio_bands = _parse_band_sequence(raw.get("ratio_bands", []), value_cast=float)

    level2_words_raw = raw.get("level2_words", {})
    if isinstance(level2_words_raw, dict):
        level2_words = _parse_level2_words(level2_words_raw)
    else:
        LOGGER.warning("masking config level2_words must be a mapping")
        level2_words = {}

    level2_divisors_raw = raw.get("level2_divisors", {})
    if isinstance(level2_divisors_raw, dict):
        level2_divisors = _parse_level2_divisors(level2_divisors_raw)
    else:
        LOGGER.warning("masking config level2_divisors must be a mapping")
        level2_divisors = {}

    ratio_thresholds_raw = raw.get("level2_ratio_thresholds", [])
    if isinstance(ratio_thresholds_raw, (list, tuple)):
        level2_ratio_thresholds = _parse_ratio_thresholds(ratio_thresholds_raw)
    else:
        LOGGER.warning("masking config level2_ratio_thresholds must be a sequence")
        level2_ratio_thresholds = ()

    return MaskingConfig(
        count_bands=(count_bands or DEFAULT_MASKING_CONFIG.count_bands),
        total_bands=(total_bands or DEFAULT_MASKING_CONFIG.total_bands),
        ratio_bands=(ratio_bands or DEFAULT_MASKING_CONFIG.ratio_bands),
        level2_words=level2_words or DEFAULT_MASKING_CONFIG.level2_words,
        level2_divisors=level2_divisors or DEFAULT_MASKING_CONFIG.level2_divisors,
        level2_ratio_thresholds=(
            level2_ratio_thresholds or DEFAULT_MASKING_CONFIG.level2_ratio_thresholds
        ),
    )


DEFAULT_MASKING_CONFIG = MaskingConfig(
    count_bands=(
        (0, 0, "0"),
        (1, 1, "1"),
        (2, 2, "2"),
        (3, 4, "3-4"),
        (5, 6, "5-6"),
        (7, 8, "7-8"),
        (9, None, "9+"),
    ),
    total_bands=(
        (0, 9, "<10"),
        (10, 19, "10-19"),
        (20, 29, "20-29"),
        (30, 49, "30-49"),
        (50, None, "50+"),
    ),
    ratio_bands=(
        (0.0, 0.39, "<40%"),
        (0.40, 0.49, "40±"),
        (0.50, 0.59, "50±"),
        (0.60, 0.69, "60±"),
        (0.70, 0.79, "70±"),
        (0.80, None, "80+%"),
    ),
    level2_words={
        "single": ("静", "穏", "賑"),
        "female": ("薄", "適", "厚"),
        "ratio": ("低", "中", "高"),
        "total": ("少", "並", "盛"),
    },
    level2_divisors={
        "single": 3,
        "female": 4,
        "total": 15,
    },
    level2_ratio_thresholds=(0.4, 0.6),
)


__all__ = [
    "Band",
    "CountBand",
    "RatioBand",
    "MaskingConfig",
    "DEFAULT_MASKING_CONFIG",
    "load_masking_config",
]

