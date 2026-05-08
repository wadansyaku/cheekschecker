#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from typing import Any, Mapping
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
    text = _clean_text(value, fallback=fallback).replace("@", "\uff20").replace("`", "'")
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

    text = _truncate(f"Workflow Failed: {workflow} ({event_name})", TOP_TEXT_MAX)
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Workflow Failed", "emoji": False},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Workflow:* {workflow}\n"
                    f"*Branch:* `{ref_name}`\n"
                    f"*Trigger:* {event_name}"
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
                        "text": {"type": "plain_text", "text": "View Logs"},
                        "url": run_url,
                    }
                ],
            }
        )

    return {"text": text, "blocks": blocks}


def main() -> int:
    print(json.dumps(build_payload(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
