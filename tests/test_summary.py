import json
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from watch_cheeks import (  # noqa: E402
    DEFAULT_ROLLOVER_HOURS,
    DailyEntry,
    Settings,
    generate_summary_payload,
    select_summary_bundle,
    update_masked_history,
)


def make_settings(**overrides):
    base = Settings(
        target_url="http://example.com",
        slack_webhook_url=None,
        female_min=3,
        female_ratio_min=0.3,
        min_total=None,
        exclude_keywords=(),
        include_dow=(),
        notify_mode="newly",
        debug_summary=False,
        ping_channel=False,
        cooldown_minutes=180,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
        ignore_older_than=1,
        notify_from_today=1,
        rollover_hours=dict(DEFAULT_ROLLOVER_HOURS),
        mask_level=1,
        robots_enforce=False,
        ua_contact=None,
    )
    if overrides:
        data = asdict(base)
        data.update(overrides)
        return Settings(**data)
    return base


def make_entry(business_day: date, single: int, female: int, male: int) -> DailyEntry:
    total = female + male
    ratio = round(female / total, 3) if total else 0.0
    dow = "Fri" if business_day.weekday() == 4 else "Mon"
    return DailyEntry(
        raw_date=business_day,
        business_day=business_day,
        day_of_month=business_day.day,
        dow_en=dow,
        male=male,
        female=female,
        single_female=single,
        total=total,
        ratio=ratio,
        considered=True,
        meets=True,
        required_single=5 if dow in {"Fri", "Sat"} else 3,
    )


def test_generate_summary_payload_and_masking(monkeypatch, tmp_path):
    logical_today = date(2024, 1, 15)
    entries = [
        make_entry(logical_today - timedelta(days=offset), single=3 + (offset % 2), female=4 + offset, male=2)
        for offset in range(7)
    ]
    settings = make_settings(mask_level=1)

    bundle = select_summary_bundle(entries, logical_today=logical_today, days=7)
    payload = generate_summary_payload(bundle, logical_today=logical_today, settings=settings)
    assert payload is not None
    assert "平均" in payload["text"]
    assert "Hot 日 Top3" in payload["text"]

    history_path = tmp_path / "history.json"
    monkeypatch.setattr("watch_cheeks.HISTORY_MASKED_PATH", history_path)
    update_masked_history(entries, settings=settings)
    data = json.loads(history_path.read_text(encoding="utf-8"))

    assert "days" in data and len(data["days"]) == len(entries)
    sample_mask = next(iter(data["days"].values()))
    assert sample_mask["single"] in {"1", "2", "3-4", "5-6", "7-8", "9+"}
