import json
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.masking import DEFAULT_MASKING_CONFIG, load_masking_config  # noqa: E402
from watch_cheeks import DailyEntry, mask_entry  # noqa: E402


def _make_entry(*, single: int, female: int, total: int, ratio: float) -> DailyEntry:
    return DailyEntry(
        raw_date=date(2024, 1, 1),
        business_day=date(2024, 1, 1),
        day_of_month=1,
        dow_en="Mon",
        male=total - female,
        female=female,
        single_female=single,
        total=total,
        ratio=ratio,
        considered=True,
        meets=True,
        required_single=0,
    )


def test_load_masking_config_missing_path(tmp_path):
    config = load_masking_config(str(tmp_path / "not-there.json"))
    assert config is DEFAULT_MASKING_CONFIG


def test_mask_entry_with_custom_config(tmp_path):
    config_data = {
        "count_bands": [[0, 1, "<=1"], [2, None, "2+"]],
        "total_bands": [[0, None, "any"]],
        "ratio_bands": [[0.0, 0.5, "low"], [0.5, None, "high"]],
        "level2_words": {
            "single": ["少", "多"],
            "female": ["薄", "厚"],
            "ratio": ["冷", "温", "熱"],
            "total": ["軽", "重"],
        },
        "level2_divisors": {"single": 2, "female": 3, "total": 5},
        "level2_ratio_thresholds": [0.25, 0.75],
    }
    config_path = tmp_path / "masking.json"
    config_path.write_text(json.dumps(config_data, ensure_ascii=False), encoding="utf-8")

    masking_config = load_masking_config(str(config_path))

    entry = _make_entry(single=5, female=9, total=15, ratio=0.6)
    masked_level1 = mask_entry(entry, mask_level=1, masking_config=masking_config)
    assert masked_level1 == {"single": "2+", "female": "2+", "total": "any", "ratio": "high"}

    masked_level2 = mask_entry(entry, mask_level=2, masking_config=masking_config)
    assert masked_level2 == {
        "single": "多",
        "female": "厚",
        "ratio": "温",
        "total": "重",
    }


def test_mask_entry_ratio_thresholds_expand(tmp_path):
    config_data = {
        "level2_words": {"ratio": ["A", "B", "C", "D"]},
        "level2_ratio_thresholds": [0.1, 0.2, 0.8],
    }
    config_path = tmp_path / "masking.json"
    config_path.write_text(json.dumps(config_data, ensure_ascii=False), encoding="utf-8")

    masking_config = load_masking_config(str(config_path))

    ratios = [0.05, 0.15, 0.5, 0.95]
    labels = [
        mask_entry(_make_entry(single=0, female=0, total=0, ratio=value), 2, masking_config)[
            "ratio"
        ]
        for value in ratios
    ]
    assert labels == ["A", "B", "C", "D"]
