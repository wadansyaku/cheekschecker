from pathlib import Path

import pytest

from src import notifications


class _Logger:
    def __init__(self) -> None:
        self.messages = []

    def debug(self, *args, **kwargs) -> None:
        self.messages.append(("debug", args, kwargs))

    def info(self, *args, **kwargs) -> None:
        self.messages.append(("info", args, kwargs))

    def warning(self, *args, **kwargs) -> None:
        self.messages.append(("warning", args, kwargs))

    def error(self, *args, **kwargs) -> None:
        self.messages.append(("error", args, kwargs))


def test_append_step_summary_writes_empty_sections_and_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))

    notifications.append_step_summary(
        "Cheeks Monitor",
        [("実行結果", [])],
        "",
        empty_fallback="該当なし",
        logger=_Logger(),
    )
    notifications.append_step_summary(
        "Cheeks Summary",
        [],
        "",
        empty_fallback="No data",
        logger=_Logger(),
    )

    content = path.read_text(encoding="utf-8")
    assert "## Cheeks Monitor" in content
    assert "- 該当なし" in content
    assert "## Cheeks Summary" in content
    assert "No data" in content


def test_append_step_summary_noops_without_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    path = tmp_path / "summary.md"

    notifications.append_step_summary(
        "Title",
        [("Section", ["line"])],
        "fallback",
        empty_fallback="none",
        logger=_Logger(),
    )

    assert not path.exists()


def test_send_slack_message_retries_fallback_when_enabled(monkeypatch) -> None:
    calls = []

    class _Response:
        def __init__(self, *, fail: bool) -> None:
            self.fail = fail
            self.status_code = 200

        def raise_for_status(self) -> None:
            if self.fail:
                raise RuntimeError("blocked")

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Response(fail=len(calls) == 1)

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    notifications.send_slack_message(
        "https://hooks.slack.test/services/example",
        {"text": "block", "blocks": []},
        "fallback text",
        logger=_Logger(),
        retry_fallback=True,
    )

    assert len(calls) == 2
    assert calls[1][1] == {"text": "fallback text"}


def test_send_slack_message_normalizes_payload_before_post(monkeypatch) -> None:
    calls = []

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Response()

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    notifications.send_slack_message(
        "https://hooks.slack.test/services/example",
        {
            "text": "primary",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "H" * 200}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "S" * 4000}},
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"context {index}"}
                        for index in range(12)
                    ],
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "B" * 100},
                            "url": "https://example.com/path",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "bad"},
                            "url": "not-a-url",
                        },
                    ],
                },
                {"type": "unsupported", "text": "drop me"},
            ],
        },
        "fallback text",
        logger=_Logger(),
    )

    posted = calls[0][1]
    assert len(posted["blocks"]) == 4
    assert len(posted["blocks"][0]["text"]["text"]) == notifications.SLACK_HEADER_TEXT_MAX
    assert len(posted["blocks"][1]["text"]["text"]) == notifications.SLACK_SECTION_TEXT_MAX
    assert len(posted["blocks"][2]["elements"]) == notifications.SLACK_MAX_CONTEXT_ELEMENTS
    assert len(posted["blocks"][3]["elements"]) == 1
    assert len(posted["blocks"][3]["elements"][0]["text"]["text"]) == notifications.SLACK_BUTTON_TEXT_MAX


def test_send_slack_message_drops_null_blocks_and_uses_clean_text(monkeypatch) -> None:
    calls = []

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, json, timeout):
        calls.append(json)
        return _Response()

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    notifications.send_slack_message(
        "https://hooks.slack.test/services/example",
        {"text": "\x00", "blocks": None},
        "fallback text",
        logger=_Logger(),
    )

    assert calls == [{"text": "fallback text"}]


def test_send_slack_message_forces_safe_text_objects(monkeypatch) -> None:
    calls = []

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, json, timeout):
        calls.append(json)
        return _Response()

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    notifications.send_slack_message(
        "https://hooks.slack.test/services/example",
        {
            "text": "block",
            "blocks": [
                {"type": "header", "text": {"type": "mrkdwn", "text": ""}},
                {"type": "section", "text": "plain string section"},
            ],
        },
        "fallback text",
        logger=_Logger(),
    )

    posted = calls[0]
    assert posted["blocks"][0]["text"]["type"] == "plain_text"
    assert posted["blocks"][0]["text"]["text"] == "Cheekschecker"
    assert posted["blocks"][1]["text"]["text"] == "plain string section"


def test_send_slack_message_truncates_fallback_retry(monkeypatch) -> None:
    calls = []

    class _Response:
        def __init__(self, *, fail: bool) -> None:
            self.fail = fail
            self.status_code = 200

        def raise_for_status(self) -> None:
            if self.fail:
                raise RuntimeError("blocked")

    def fake_post(url, json, timeout):
        calls.append(json)
        return _Response(fail=len(calls) == 1)

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    notifications.send_slack_message(
        "https://hooks.slack.test/services/example",
        {"text": "block", "blocks": [{"type": "section", "text": {"text": "ok"}}]},
        "F" * 41000,
        logger=_Logger(),
        retry_fallback=True,
    )

    assert len(calls) == 2
    assert len(calls[1]["text"]) == notifications.SLACK_TOP_LEVEL_TEXT_MAX
    assert calls[1]["text"].endswith(notifications.SLACK_TRUNCATION_SUFFIX)


def test_send_slack_message_redacts_webhook_from_failure_logs_and_exception(monkeypatch) -> None:
    logger = _Logger()
    webhook = "https://hooks.slack.com/services/T000/B000/SECRET"

    class _Response:
        status_code = 500
        text = f"invalid_payload for {webhook}"

    def fake_post(url, json, timeout):
        error = notifications.requests.exceptions.HTTPError(
            f"500 Server Error for url: {webhook}"
        )
        error.response = _Response()
        raise error

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    with pytest.raises(RuntimeError) as exc_info:
        notifications.send_slack_message(
            webhook,
            {"text": "block", "blocks": []},
            "fallback text",
            logger=logger,
            retry_fallback=False,
            raise_on_failure=True,
        )

    logged = repr(logger.messages)
    assert webhook not in str(exc_info.value)
    assert webhook not in logged
    assert "[redacted slack webhook]" in logged


def test_send_slack_message_redacts_no_webhook_fallback_preview() -> None:
    logger = _Logger()
    webhook = "https://hooks.slack.com/services/T000/B000/SECRET"

    notifications.send_slack_message(
        None,
        {"text": webhook, "blocks": []},
        webhook,
        logger=logger,
    )

    logged = repr(logger.messages)
    assert webhook not in logged
    assert "[redacted slack webhook]" in logged


def test_build_simple_slack_payload_coerces_and_limits_text() -> None:
    class BadStr:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    payload, fallback = notifications.build_simple_slack_payload("M" * 5000, 123)
    assert fallback.startswith("123 ")
    assert len(payload["blocks"][0]["text"]["text"]) == notifications.SLACK_SECTION_TEXT_MAX
    assert payload["blocks"][0]["text"]["text"].endswith(
        notifications.SLACK_TRUNCATION_SUFFIX
    )

    payload, fallback = notifications.build_simple_slack_payload(BadStr(), BadStr())
    assert fallback == "Cheekschecker Notification detail unavailable"
    assert payload["text"] == fallback


def test_send_slack_message_skips_fallback_when_disabled(monkeypatch) -> None:
    calls = []

    class _Response:
        status_code = 500

        def raise_for_status(self) -> None:
            raise RuntimeError("blocked")

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Response()

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    notifications.send_slack_message(
        "https://hooks.slack.test/services/example",
        {"text": "block", "blocks": []},
        "fallback text",
        logger=_Logger(),
        retry_fallback=False,
    )

    assert len(calls) == 1


def test_send_slack_message_strict_mode_fails_without_webhook() -> None:
    with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL"):
        notifications.send_slack_message(
            None,
            {"text": "block", "blocks": []},
            "fallback text",
            logger=_Logger(),
            raise_on_failure=True,
        )
