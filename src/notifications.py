"""Shared Slack and GitHub Step Summary helpers."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Sequence, Tuple

import requests


LOGGER = logging.getLogger(__name__)

StepSummarySections = Sequence[Tuple[str, Sequence[str]]]


def _logger_or_default(logger: Optional[Any]) -> Any:
    return logger if logger is not None else LOGGER


def append_step_summary(
    title: str,
    sections: StepSummarySections,
    fallback: str,
    *,
    empty_fallback: str,
    logger: Optional[Any] = None,
) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    log = _logger_or_default(logger)
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
                        handle.write(f"- {empty_fallback}\n")
                    handle.write("\n")
            else:
                handle.write(f"{fallback or empty_fallback}\n\n")
    except OSError as exc:  # pragma: no cover - filesystem edge cases
        log.debug("Failed to append step summary: %s", exc)


def send_slack_message(
    webhook: Optional[str],
    payload: Dict[str, Any],
    fallback_text: str,
    *,
    logger: Optional[Any] = None,
    retry_fallback: bool = True,
    timeout: int = 10,
) -> None:
    log = _logger_or_default(logger)
    if not webhook:
        log.warning("SLACK_WEBHOOK_URL is not set; skipping Slack notification")
        log.info("Fallback summary (no webhook): %s", fallback_text)
        return

    try:
        response = requests.post(webhook, json=payload, timeout=timeout)
        response.raise_for_status()
        log.info("Slack notification sent via block kit")
        return
    except Exception as exc:  # pragma: no cover - network variability
        log.error("Slack block send failed: %s", exc)

    if not retry_fallback:
        return

    try:
        response = requests.post(webhook, json={"text": fallback_text}, timeout=timeout)
        response.raise_for_status()
        log.info("Slack fallback text sent")
    except Exception as exc:  # pragma: no cover - network variability
        log.error("Slack fallback also failed: %s", exc)


def build_simple_slack_payload(message: str, title: str) -> Tuple[Dict[str, Any], str]:
    fallback = f"{title} {message}"
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}*\n{message}"},
            }
        ],
        "text": fallback,
    }
    return payload, fallback


def send_simple_message(
    webhook: Optional[str],
    message: str,
    title: str,
    *,
    logger: Optional[Any] = None,
) -> None:
    payload, fallback = build_simple_slack_payload(message, title)
    send_slack_message(webhook, payload, fallback, logger=logger)


__all__ = [
    "StepSummarySections",
    "append_step_summary",
    "build_simple_slack_payload",
    "send_simple_message",
    "send_slack_message",
]
