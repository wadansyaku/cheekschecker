#!/usr/bin/env python3
"""Generate public-safe weekly/monthly summaries with masked archives and Slack output."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from src.logging_config import configure_logging, get_logger
from src.notifications import (
    append_step_summary as _append_step_summary,
    build_simple_slack_payload,
    send_slack_message as _send_slack_message,
)
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
    _append_step_summary(
        title,
        sections,
        fallback,
        empty_fallback="該当なし",
        logger=LOGGER,
    )


def send_slack_message(
    webhook: str,
    payload: Dict[str, Any],
    fallback_text: str,
    *,
    strict: bool = False,
) -> None:
    _send_slack_message(
        webhook,
        payload,
        fallback_text,
        logger=LOGGER,
        raise_on_failure=strict,
    )


def send_simple_message(webhook: str, message: str, title: str, *, strict: bool = False) -> None:
    payload, fallback = build_simple_slack_payload(message, title)
    send_slack_message(webhook, payload, fallback, strict=strict)


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


def handle_source_unavailable(
    period_title: str,
    webhook: str,
    summary_title: str,
    detail: Optional[str] = None,
) -> None:
    message = "source unavailable / 外部サイト取得失敗"
    if detail:
        message = f"{message} ({detail})"
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

    if dataset.fetch_status != "ok":
        LOGGER.warning(
            "Skipping summary generation because source was unavailable: %s",
            dataset.fetch_error or dataset.fetch_status,
        )
        store = load_summary_store(args.output)
        store[args.period] = build_masked_summary(
            None,
            history_meta=history_meta,
            status="source-unavailable",
        )
        save_summary_store(args.output, store)
        handle_source_unavailable(
            period_title,
            webhook,
            summary_title,
            dataset.fetch_error,
        )
        return 0

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
    send_simple_message(webhook, "Webhook OK", "Cheekschecker: Webhook OK", strict=True)
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
