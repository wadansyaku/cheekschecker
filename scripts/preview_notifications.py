#!/usr/bin/env python3
"""Render synthetic Slack payloads locally without fetching or sending anything."""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.notifications import normalize_slack_payload
from src.public_summary import (
    DailyRecord,
    RawDataset,
    build_placeholder_summary_payload,
    build_slack_payload,
    build_summary_context,
)
from watch_cheeks import (
    DEFAULT_ROLLOVER_HOURS,
    Settings,
    _build_monitor_diagnostic_entry,
    _build_slack_blocks,
)


def synthetic_payloads() -> dict[str, dict[str, Any]]:
    """Use only invented observations and an intentionally non-live calendar URL."""
    today = date(2026, 10, 15)
    settings = Settings(
        target_url="https://example.invalid/calendar",
        slack_webhook_url=None,
        female_min=3,
        female_ratio_min=0.3,
        min_total=None,
        exclude_keywords=(),
        include_dow=(),
        notify_mode="newly",
        ping_channel=True,
        cooldown_minutes=180,
        bonus_single_delta=2,
        bonus_ratio_threshold=0.5,
        ignore_older_than=1,
        notify_from_today=1,
        rollover_hours=dict(DEFAULT_ROLLOVER_HOURS),
        mask_level=1,
        robots_enforce=True,
        ua_contact=None,
        allow_fetch_failure=False,
        head_skip_max_age_minutes=180,
    )
    entry = _build_monitor_diagnostic_entry(today)
    tomorrow = replace(entry, business_day=today + timedelta(days=1))
    monitor = {
        "text": "架空の予約状況更新",
        "blocks": _build_slack_blocks(
            [(entry, "initial"), (tomorrow, "bonus")],
            [],
            [],
            today,
            settings,
            observed_at="2026-10-15T18:40:00+09:00",
        ),
    }
    dataset = RawDataset(
        period_label="過去7日",
        window_days=7,
        logical_today=today,
        current=[
            DailyRecord(today - timedelta(days=i), 3 + i % 2, 6, 15, 0.4)
            for i in range(4)
        ],
        previous=[],
    )
    context = build_summary_context("weekly", dataset, {"days": {}})
    assert context is not None
    weekly, _, _ = build_slack_payload(context, "週次サマリー")
    unavailable, _, _ = build_placeholder_summary_payload(
        "Cheekschecker 週次サマリー",
        "外部サイト取得失敗",
        source_unavailable=True,
    )
    empty, _, _ = build_placeholder_summary_payload(
        "Cheekschecker 週次サマリー",
        "集計対象なし",
    )
    return {
        "予約状況の更新": monitor,
        "週次・データ不足": weekly,
        "取得失敗": unavailable,
        "観測なし": empty,
    }


def render_text(value: dict[str, Any]) -> str:
    text = html.escape(value.get("text", ""))
    if value.get("type") == "mrkdwn":
        import re

        text = re.sub(r"\*([^*\n]+)\*", r"<strong>\1</strong>", text)
    return text.replace("\n", "<br>")


def render_payload(payload: dict[str, Any]) -> str:
    payload, _ = normalize_slack_payload(payload, str(payload.get("text", "")))
    parts = []
    for block in payload.get("blocks", []):
        kind = block["type"]
        if kind == "header":
            parts.append(f'<h2>{render_text(block["text"])}</h2>')
        elif kind == "context":
            parts.append(
                '<p class="context">'
                + "<br>".join(render_text(item) for item in block["elements"])
                + "</p>"
            )
        elif kind == "section":
            if "text" in block:
                parts.append(f'<p>{render_text(block["text"])}</p>')
            if "fields" in block:
                parts.append(
                    '<div class="fields">'
                    + "".join(f"<p>{render_text(item)}</p>" for item in block["fields"])
                    + "</div>"
                )
        elif kind == "divider":
            parts.append("<hr>")
        elif kind == "actions":
            # The preview deliberately has no live external actions.
            parts.append(
                '<div class="actions">'
                + "".join(
                    f'<button type="button" aria-disabled="true" title="プレビューのため移動しません">{render_text(item["text"])}</button>'
                    for item in block["elements"]
                )
                + "</div>"
            )
    return "\n".join(parts)


def build_preview() -> str:
    payloads = synthetic_payloads()
    buttons = "".join(
        f'<button type="button" data-scene="s{i}" aria-pressed="{str(i == 0).lower()}">{html.escape(label)}</button>'
        for i, label in enumerate(payloads)
    )
    scenes = "".join(
        f'<article id="s{i}" class="message" {"" if i == 0 else "hidden"}>{render_payload(payload)}</article>'
        for i, payload in enumerate(payloads.values())
    )
    return (
        """<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cheekschecker · 通知プレビュー</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f4f1;color:#242728;font:15px/1.8 -apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",sans-serif}
main{max-width:1100px;margin:56px auto;padding:0 24px}.eyebrow{font-size:12px;letter-spacing:.12em;color:#57746c}h1{font-size:32px;letter-spacing:-.04em;margin:12px 0}header>p{color:#636b68;max-width:720px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:28px 0 12px}button{font:inherit;line-height:1.4;padding:10px 16px;border:1px solid #d7dcd9;border-radius:8px;background:#fff;color:#263c35;cursor:pointer}button[aria-pressed=true]{background:#244c40;color:white;border-color:#244c40}button:focus-visible{outline:3px solid #557ce0;outline-offset:3px}.tools{display:flex;gap:8px;align-items:center;margin-bottom:24px}.tools span{font-size:12px;color:#636b68;margin-right:auto}.tools button{font-size:12px;padding:6px 12px}.frame{max-width:820px;transition:max-width .2s}.frame.mobile{max-width:375px}.message{padding:28px 32px;background:white;border:1px solid #e0e3df;border-radius:12px;box-shadow:0 10px 28px #253f3010;overflow-wrap:anywhere}.message[hidden]{display:none}h2{font-size:19px;line-height:1.5;margin:0 0 16px}.context{font-size:12px;color:#626a67}.fields{display:grid;grid-template-columns:1fr 1fr;gap:20px}.actions{display:flex;flex-wrap:wrap;gap:10px;border-top:1px solid #eceeea;padding-top:20px;margin-top:24px}.actions button{font-size:13px}.mobile .fields{grid-template-columns:1fr;gap:0}.mobile .message{padding:20px}footer{font-size:12px;color:#69716e;margin-top:28px}@media(max-width:600px){main{margin:28px auto;padding:0 16px}h1{font-size:26px}.message{padding:20px}.fields{grid-template-columns:1fr;gap:0}}@media(prefers-reduced-motion:reduce){.frame{transition:none}}
</style>
<main><header><div class="eyebrow">CHEEKSCHECKER / NOTIFICATION REVIEW</div><h1>必要な変化が、すぐにわかる。</h1><p>実装と同じ通知データを使い、情報の順序・日付・データ不足の伝わり方を確認できます。</p></header>
<nav aria-label="通知シナリオ">"""
        + buttons
        + """</nav><div class="tools"><span>架空データのみ · 実送信なし</span><button id="wide" aria-pressed="true">標準幅</button><button id="mobile" aria-pressed="false">375px</button></div>
<div class="frame" aria-live="polite">"""
        + scenes
        + """</div><footer>Slack Block Kitの内容をHTMLで近似表示しています。実際のSlackの文字組み、通知バナー、読み上げは別途確認が必要です。ボタンの外部移動は無効です。</footer></main>
<script>
document.querySelectorAll('[data-scene]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-scene]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));document.querySelectorAll('article').forEach(a=>a.hidden=a.id!==button.dataset.scene)}));
['wide','mobile'].forEach(id=>document.getElementById(id).addEventListener('click',()=>{document.querySelector('.frame').classList.toggle('mobile',id==='mobile');['wide','mobile'].forEach(x=>document.getElementById(x).setAttribute('aria-pressed',String(x===id)))}));
</script></html>"""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "notification_preview.html"
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_preview(), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
