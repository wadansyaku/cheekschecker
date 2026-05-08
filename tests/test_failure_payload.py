import json
import subprocess
import sys

from scripts.ci import build_slack_failure_payload


def _payload_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_build_payload_sanitizes_mrkdwn_and_drops_unsafe_run_url() -> None:
    payload = build_slack_failure_payload.build_payload(
        {
            "WORKFLOW_NAME": "監視失敗",
            "REF_NAME": "feature/@channel `bad`\x00\u202e <@U123> & <tag>",
            "EVENT_NAME": "pull_request\n@here",
            "RUN_URL": "javascript:alert(1)",
        }
    )

    text = _payload_text(payload)
    assert "監視失敗" in text
    assert "@channel" not in text
    assert "@here" not in text
    assert "`bad`" not in text
    assert "<@U123>" not in text
    assert "javascript:" not in text
    assert "&amp;" in text
    assert "&lt;tag&gt;" in text
    assert all(block["type"] != "actions" for block in payload["blocks"])


def test_build_payload_includes_valid_run_url_button() -> None:
    run_url = "https://github.com/example/repo/actions/runs/123"
    payload = build_slack_failure_payload.build_payload(
        {
            "WORKFLOW_NAME": "Monitor Calendar",
            "REF_NAME": "main",
            "EVENT_NAME": "schedule",
            "RUN_URL": run_url,
        }
    )

    actions = [block for block in payload["blocks"] if block["type"] == "actions"]
    assert actions[0]["elements"][0]["url"] == run_url


def test_cli_writes_utf8_json_without_ascii_escaping(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_NAME", "月次サマリー")
    monkeypatch.setenv("REF_NAME", "main")
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("RUN_URL", "https://github.com/example/repo/actions/runs/123")

    result = subprocess.run(
        [sys.executable, "scripts/ci/build_slack_failure_payload.py"],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert "月次サマリー" in result.stdout
    assert "\\u6708" not in result.stdout
    parsed = json.loads(result.stdout)
    assert parsed["text"].startswith("Workflow Failed:")
