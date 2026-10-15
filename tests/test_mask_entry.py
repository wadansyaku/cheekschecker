import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from watch_cheeks import DailyEntry, MASK_LEVEL2_WORDS, mask_entry  # noqa: E402


def make_entry(ratio: float) -> DailyEntry:
    return DailyEntry(
        raw_date=date(2024, 1, 1),
        business_day=date(2024, 1, 1),
        day_of_month=1,
        dow_en="Mon",
        male=0,
        female=0,
        single_female=0,
        total=0,
        ratio=ratio,
        considered=False,
        meets=False,
        required_single=0,
    )


def test_mask_level2_ratio_bins_cover_all_labels():
    ratios = (0.3, 0.5, 0.7)
    labels = {mask_entry(make_entry(ratio), mask_level=2)["ratio"] for ratio in ratios}
    assert labels == set(MASK_LEVEL2_WORDS["ratio"])


@pytest.mark.parametrize("value,expected", [(0.0, "<40%"), (0.395, "<40%"), (0.4, "40±"), (0.495, "40±"), (0.595, "50±"), (0.695, "60±"), (0.795, "70±"), (0.8, "80+%"), (1.0, "80+%")])
def test_fractional_ratio_never_falls_through_to_highest_band(value, expected):
    from src.masking import DEFAULT_MASKING_CONFIG
    from src.public_summary import _bin_ratio

    assert mask_entry(make_entry(value), mask_level=1)["ratio"] == expected
    assert _bin_ratio(value, DEFAULT_MASKING_CONFIG.ratio_bands) == expected
