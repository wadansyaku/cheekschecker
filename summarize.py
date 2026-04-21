#!/usr/bin/env python3
"""Generate public-safe weekly/monthly summaries with masked archives and Slack output."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from src.logging_config import configure_logging, get_logger
from src.public_state import load_masked_history, load_summary_store, save_summary_store
from src.public_summary import (
    DailyRecord,
    JST,
    RawDataset,
    build_masked_summary,
    build_placeholder_summary_payload,
    build_slack_payload,
    build_summary_context,
    load_raw_dataset,
)


configure_logging(debug=bool(int(os.getenv("DEBUG_LOG", "0"))))
LOGGER = get_logger(__name__)

STEP_SUMMARY_TITLES = {
    "weekly": "Cheeks Weekly Summary",
    "monthly": "Cheeks Monthly Summary",
}


def append_step_summary(title: str, sections: Sequence[Tuple[str, Sequence[str]]], fallback: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"## {title}\n\n")
            if sections:
                for heading, lines in sections:
                    handle.write(f"### {heading}\n\n")
                    if lines:
                        for line in lines:
                            handle.write(f"- {line}\n")
                    else:
                        handle.write("- 該当なし\n")
                    handle.write("\n")
            else:
                handle.write(f"{fallback or 'No data'}\n\n")
    except OSError as exc:  # pragma: no cover - filesystem nuances
        LOGGER.debug("Failed to append step summary: %s", exc)


def send_slack_message(webhook: str, payload: Dict[str, Any], fallback_text: str) -> None:
    if not webhook:
        LOGGER.warning("SLACK_WEBHOOK_URL is not set; skipping Slack notification")
        LOGGER.info("Fallback summary (no webhook): %s", fallback_text)
        return
    try:
        response = requests.post(webhook, json=payload, timeout=10)
        response.raise_for_status()
        LOGGER.info("Slack notification sent via block kit")
        return
    except Exception as exc:
        LOGGER.error("Slack block send failed: %s", exc)
    try:
        response = requests.post(webhook, json={"text": fallback_text}, timeout=10)
        response.raise_for_status()
        LOGGER.info("Slack fallback text sent")
    except Exception as exc:
        LOGGER.error("Slack fallback also failed: %s", exc)


def send_simple_message(webhook: str, message: str, title: str) -> None:
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}*\n{message}"},
            }
        ],
        "text": f"{title} {message}",
    }
    send_slack_message(webhook, payload, f"{title} {message}")


def handle_no_data(period_title: str, webhook: str, summary_title: str) -> None:
    message = "No data for this period / 集計対象なし"
    title = f"Cheekschecker {period_title}"
    payload, fallback, sections = build_placeholder_summary_payload(title, message)
    append_step_summary(summary_title, sections, fallback)
    send_slack_message(webhook, payload, fallback)


def handle_broken_data(period_title: str, webhook: str, summary_title: str) -> None:
    message = "public-safe summary could not be built / 集計対象なし"
    title = f"Cheekschecker {period_title}"
    payload, fallback, sections = build_placeholder_summary_payload(title, message)
    append_step_summary(summary_title, sections, fallback)
    send_slack_message(webhook, payload, fallback)


def run_summary(args: argparse.Namespace) -> int:
    history_meta = load_masked_history(args.history)
    dataset = load_raw_dataset(args.raw_data)
    period_title = "週次サマリー" if args.period == "weekly" else "月次サマリー"
    webhook = args.slack_webhook
    summary_title = STEP_SUMMARY_TITLES.get(args.period, period_title)

    try:
        context = build_summary_context(args.period, dataset, history_meta)
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.exception("Failed to build public-safe summary context: %s", exc)
        context = None

    store = load_summary_store(args.output)
    store[args.period] = build_masked_summary(context, history_meta=history_meta)
    save_summary_store(args.output, store)

    if context is None:
        if dataset.current:
            handle_broken_data(period_title, webhook, summary_title)
        else:
            handle_no_data(period_title, webhook, summary_title)
        return 0

    payload, fallback_text, summary_sections = build_slack_payload(
        context,
        f"Cheekschecker {period_title}",
        logical_today=dataset.logical_today,
    )
    append_step_summary(summary_title, summary_sections, fallback_text)
    send_slack_message(webhook, payload, fallback_text)
    return 0


def run_ping(args: argparse.Namespace) -> int:
    webhook = args.slack_webhook
    send_simple_message(webhook, "Webhook OK", "Cheekschecker: Webhook OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cheekschecker summary helper")
    parser.add_argument("--period", choices=["weekly", "monthly"], required=False, default="weekly")
    parser.add_argument("--raw-data", type=Path, dest="raw_data")
    parser.add_argument("--history", type=Path, default=Path("history_masked.json"))
    parser.add_argument("--output", type=Path, default=Path("summary_masked.json"))
    parser.add_argument("--ping-only", action="store_true")
    parser.add_argument("--slack-webhook", dest="slack_webhook", default=os.getenv("SLACK_WEBHOOK_URL", ""))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.ping_only:
        return run_ping(args)
    if not args.raw_data:
        LOGGER.warning("--raw-data not provided; treating as no data")
        period_title = "週次サマリー" if args.period == "weekly" else "月次サマリー"
        summary_title = STEP_SUMMARY_TITLES.get(args.period, period_title)
        handle_no_data(period_title, args.slack_webhook, summary_title)
        return 0
    return run_summary(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
