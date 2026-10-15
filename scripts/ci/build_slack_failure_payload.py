#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

FIELD_TEXT_MAX = 180
TOP_TEXT_MAX = 1000


def _clean_text(value: str | None, *, fallback: str) -> str:
    text = value if value else fallback
    chars: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf" or ord(char) < 32:
            chars.append(" ")
        else:
            chars.append(char)
    cleaned = re.sub(r"\s+", " ", "".join(chars)).strip()
    return cleaned or fallback


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    suffix = "... [truncated]"
    return text[: max_length - len(suffix)] + suffix


def _escape_mrkdwn(value: str | None, *, fallback: str) -> str:
    text = (
        _clean_text(value, fallback=fallback).replace("@", "\uff20").replace("`", "'")
    )
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _truncate(text, FIELD_TEXT_MAX)


def _valid_run_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value.strip()


def build_payload(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    workflow = _escape_mrkdwn(source.get("WORKFLOW_NAME"), fallback="Cheekschecker")
    ref_name = _escape_mrkdwn(source.get("REF_NAME"), fallback="unknown")
    event_name = _escape_mrkdwn(source.get("EVENT_NAME"), fallback="unknown")
    run_url = _valid_run_url(source.get("RUN_URL"))
    diagnostic = source.get("FAILURE_DIAGNOSTIC") == "1"

    title = (
        "【診断】障害通知の動作確認"
        if diagnostic
        else "Cheekschecker 処理に失敗しました"
    )
    guidance = (
        "意図的に失敗させたテストです。実カレンダーの取得や監視状態の更新は行っていません。"
        if diagnostic
        else "通知の最新確認時刻をご確認ください。急ぐ場合はカレンダーを直接確認し、実行ログで原因を確認してください。"
    )
    text = _truncate(f"{title}: {workflow} ({event_name})\n{guidance}", TOP_TEXT_MAX)
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title, "emoji": False},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{guidance}\n*処理:* {workflow}\n"
                    f"*ブランチ:* `{ref_name}`\n"
                    f"*起動方法:* {event_name}"
                ),
            },
        },
    ]

    if run_url is not None:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "実行ログを確認"},
                        "url": run_url,
                    }
                ],
            }
        )

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "カレンダーを開く"},
                    "url": "http://cheeks.nagoya/yoyaku.shtml",
                }
            ],
        }
    )
    return {
        "text": text,
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }


def main() -> int:
    print(json.dumps(build_payload(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
