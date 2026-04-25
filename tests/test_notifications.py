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
