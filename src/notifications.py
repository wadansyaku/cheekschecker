"""Shared Slack and GitHub Step Summary helpers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional, Sequence, Tuple

import requests


LOGGER = logging.getLogger(__name__)

StepSummarySections = Sequence[Tuple[str, Sequence[str]]]

SLACK_MAX_BLOCKS = 50
SLACK_MAX_BLOCK_ELEMENTS = 25
SLACK_MAX_CONTEXT_ELEMENTS = 10
SLACK_TOP_LEVEL_TEXT_MAX = 40000
SLACK_SECTION_TEXT_MAX = 3000
SLACK_FIELD_TEXT_MAX = 2000
SLACK_CONTEXT_TEXT_MAX = 3000
SLACK_HEADER_TEXT_MAX = 150
SLACK_BUTTON_TEXT_MAX = 75
SLACK_BUTTON_URL_MAX = 3000
SLACK_TRUNCATION_SUFFIX = "... [truncated]"
SLACK_LOG_PREVIEW_MAX = 500
SLACK_DEFAULT_FALLBACK = "Cheekschecker notification"
SLACK_DEFAULT_SECTION_TEXT = "Notification detail unavailable"
_SLACK_WEBHOOK_RE = re.compile(
    r"https://hooks\.slack(?:-gov)?(?:\.com|\.test)/services/[^\s)>\]\"']+"
)
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def _logger_or_default(logger: Optional[Any]) -> Any:
    return logger if logger is not None else LOGGER


def _clean_text(value: Any, *, fallback: str = " ") -> str:
    if value is None:
        text = fallback
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = str(value)
        except Exception:
            text = fallback
    cleaned = "".join(
        ch if ch in {"\n", "\t"} or ord(ch) >= 32 else " " for ch in text
    ).strip()
    return cleaned or fallback


def _truncate_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    if max_length <= len(SLACK_TRUNCATION_SUFFIX):
        return text[:max_length]
    return text[: max_length - len(SLACK_TRUNCATION_SUFFIX)] + SLACK_TRUNCATION_SUFFIX


def _normalize_text_object(
    value: Any,
    *,
    max_length: int,
    default_type: str = "mrkdwn",
    fallback: str = SLACK_DEFAULT_SECTION_TEXT,
    force_type: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        value = {"text": value}
    text_type = value.get("type")
    if force_type is not None:
        text_type = force_type
    elif text_type not in {"mrkdwn", "plain_text"}:
        text_type = default_type
    text = _truncate_text(
        _clean_text(value.get("text"), fallback=fallback),
        max_length,
    )
    normalized: Dict[str, Any] = {"type": text_type, "text": text}
    if text_type == "plain_text" and isinstance(value.get("emoji"), bool):
        normalized["emoji"] = value["emoji"]
    if text_type == "mrkdwn" and isinstance(value.get("verbatim"), bool):
        normalized["verbatim"] = value["verbatim"]
    return normalized


def _normalize_button(element: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = _normalize_text_object(
        element.get("text"),
        max_length=SLACK_BUTTON_TEXT_MAX,
        default_type="plain_text",
        fallback="Open",
        force_type="plain_text",
    )
    normalized: Dict[str, Any] = {"type": "button", "text": text}

    url = element.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        normalized["url"] = _truncate_text(url, SLACK_BUTTON_URL_MAX)

    action_id = element.get("action_id")
    if isinstance(action_id, str) and action_id.strip():
        normalized["action_id"] = _truncate_text(_clean_text(action_id), 255)

    value = element.get("value")
    if isinstance(value, str) and value.strip():
        normalized["value"] = _truncate_text(_clean_text(value), 2000)

    if "url" not in normalized and "action_id" not in normalized and "value" not in normalized:
        return None
    return normalized


def _normalize_block(block: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(block, dict):
        return None
    block_type = block.get("type")

    if block_type == "header":
        return {
            "type": "header",
            "text": _normalize_text_object(
                block.get("text"),
                max_length=SLACK_HEADER_TEXT_MAX,
                default_type="plain_text",
                fallback="Cheekschecker",
                force_type="plain_text",
            ),
        }

    if block_type == "context":
        elements = []
        raw_elements = block.get("elements")
        if not isinstance(raw_elements, (list, tuple)):
            raw_elements = []
        for element in raw_elements[:SLACK_MAX_CONTEXT_ELEMENTS]:
            if not isinstance(element, dict):
                continue
            if element.get("type") in {"mrkdwn", "plain_text"}:
                elements.append(
                    _normalize_text_object(
                        element,
                        max_length=SLACK_CONTEXT_TEXT_MAX,
                        default_type=str(element.get("type") or "mrkdwn"),
                    )
                )
        if not elements:
            return None
        return {"type": "context", "elements": elements}

    if block_type == "section":
        normalized: Dict[str, Any] = {"type": "section"}
        fields = block.get("fields")
        if isinstance(fields, (list, tuple)) and fields:
            normalized_fields = [
                _normalize_text_object(
                    field,
                    max_length=SLACK_FIELD_TEXT_MAX,
                    default_type="mrkdwn",
                )
                for field in fields[:10]
            ]
            if normalized_fields:
                normalized["fields"] = normalized_fields
        if "fields" not in normalized:
            normalized["text"] = _normalize_text_object(
                block.get("text"),
                max_length=SLACK_SECTION_TEXT_MAX,
                default_type="mrkdwn",
            )
        return normalized

    if block_type == "actions":
        elements = []
        raw_elements = block.get("elements")
        if isinstance(raw_elements, (list, tuple)):
            for element in raw_elements[:SLACK_MAX_BLOCK_ELEMENTS]:
                if isinstance(element, dict) and element.get("type") == "button":
                    button = _normalize_button(element)
                    if button is not None:
                        elements.append(button)
        if not elements:
            return None
        return {"type": "actions", "elements": elements}

    return None


def normalize_slack_payload(
    payload: Dict[str, Any],
    fallback_text: str,
) -> Tuple[Dict[str, Any], str]:
    """Return a Slack payload constrained to common Block Kit limits."""
    if not isinstance(payload, dict):
        payload = {}

    fallback = _truncate_text(
        _clean_text(fallback_text, fallback="Cheekschecker notification"),
        SLACK_TOP_LEVEL_TEXT_MAX,
    )
    top_text = _truncate_text(
        _clean_text(payload.get("text"), fallback=fallback),
        SLACK_TOP_LEVEL_TEXT_MAX,
    )
    normalized: Dict[str, Any] = {"text": top_text}

    raw_blocks = payload.get("blocks")
    normalized_blocks = []
    if isinstance(raw_blocks, (list, tuple)):
        for block in raw_blocks[:SLACK_MAX_BLOCKS]:
            normalized_block = _normalize_block(block)
            if normalized_block is not None:
                normalized_blocks.append(normalized_block)

    if normalized_blocks:
        normalized["blocks"] = normalized_blocks
    for key in ("unfurl_links", "unfurl_media", "mrkdwn"):
        if isinstance(payload.get(key), bool):
            normalized[key] = payload[key]
    for key in ("username", "icon_url", "icon_emoji", "channel"):
        max_length = SLACK_BUTTON_URL_MAX if key == "icon_url" else 255
        value = _clean_text(payload.get(key), fallback="")
        if value:
            normalized[key] = _truncate_text(value, max_length)

    return normalized, fallback


def _redact_urls(text: str) -> str:
    text = _SLACK_WEBHOOK_RE.sub("[redacted slack webhook]", text)
    return _URL_RE.sub("[redacted url]", text)


def _safe_log_preview(value: Any, *, max_length: int = SLACK_LOG_PREVIEW_MAX) -> str:
    return _redact_urls(_truncate_text(_clean_text(value), max_length))


def _exception_summary(exc: BaseException) -> str:
    message = _redact_urls(_clean_text(exc, fallback=exc.__class__.__name__))
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    response_text = _clean_text(getattr(response, "text", ""), fallback="")
    if response_text:
        response_text = _safe_log_preview(response_text, max_length=220)
    if status_code is not None and response_text:
        message = f"{message} (status={status_code}, body={response_text})"
    elif status_code is not None:
        message = f"{message} (status={status_code})"
    return _safe_log_preview(message, max_length=600)


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
    raise_on_failure: bool = False,
) -> None:
    log = _logger_or_default(logger)
    payload, fallback_text = normalize_slack_payload(payload, fallback_text)
    if not webhook:
        log.warning("SLACK_WEBHOOK_URL is not set; skipping Slack notification")
        log.info(
            "Fallback summary (no webhook, %d chars): %s",
            len(fallback_text),
            _safe_log_preview(fallback_text),
        )
        if raise_on_failure:
            raise RuntimeError("SLACK_WEBHOOK_URL is not set")
        return

    block_error: Exception | None = None
    try:
        response = requests.post(webhook, json=payload, timeout=timeout)
        response.raise_for_status()
        log.info("Slack notification sent via block kit")
        return
    except Exception as exc:  # pragma: no cover - network variability
        block_error = exc
        log.error("Slack block send failed: %s", _exception_summary(exc))

    if not retry_fallback:
        if raise_on_failure:
            detail = _exception_summary(block_error) if block_error else "unknown error"
            raise RuntimeError(f"Slack block notification failed: {detail}") from None
        return

    try:
        response = requests.post(webhook, json={"text": fallback_text}, timeout=timeout)
        response.raise_for_status()
        log.info("Slack fallback text sent")
    except Exception as exc:  # pragma: no cover - network variability
        log.error("Slack fallback also failed: %s", _exception_summary(exc))
        if raise_on_failure:
            raise RuntimeError(
                f"Slack block and fallback notifications failed: {_exception_summary(exc)}"
            ) from None


def build_simple_slack_payload(message: str, title: str) -> Tuple[Dict[str, Any], str]:
    safe_title = _truncate_text(
        _clean_text(title, fallback="Cheekschecker"),
        SLACK_HEADER_TEXT_MAX,
    )
    message_max = max(SLACK_SECTION_TEXT_MAX - len(safe_title) - 3, 1)
    safe_message = _truncate_text(
        _clean_text(message, fallback=SLACK_DEFAULT_SECTION_TEXT),
        message_max,
    )
    fallback = _truncate_text(
        _clean_text(f"{safe_title} {safe_message}", fallback=SLACK_DEFAULT_FALLBACK),
        SLACK_TOP_LEVEL_TEXT_MAX,
    )
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{safe_title}*\n{safe_message}"},
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
    "normalize_slack_payload",
    "send_simple_message",
    "send_slack_message",
]
