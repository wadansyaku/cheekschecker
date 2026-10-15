import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.ci import push_with_retry


def _git_result(code: int, error: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git"], code, "", error)


def test_transient_push_failure_retries_the_same_commit(monkeypatch) -> None:
    run = Mock(
        side_effect=[_git_result(128, "remote: Internal Server Error"), _git_result(0)]
    )
    sleep = Mock()
    monkeypatch.setattr(push_with_retry.subprocess, "run", run)
    monkeypatch.setattr(push_with_retry.time, "sleep", sleep)

    assert push_with_retry.push_with_retry("main") == 0

    assert run.call_count == 2
    assert run.call_args_list[0] == run.call_args_list[1]
    assert run.call_args.args[0] == [
        "git",
        "push",
        "--porcelain",
        "origin",
        "HEAD:refs/heads/main",
    ]
    sleep.assert_called_once_with(5)


def test_transient_push_failure_stops_after_three_attempts(monkeypatch) -> None:
    run = Mock(
        return_value=_git_result(128, "fatal: The requested URL returned error: 503")
    )
    sleep = Mock()
    monkeypatch.setattr(push_with_retry.subprocess, "run", run)
    monkeypatch.setattr(push_with_retry.time, "sleep", sleep)

    assert push_with_retry.push_with_retry("main") == 128

    assert run.call_count == 3
    assert [call.args for call in sleep.call_args_list] == [(5,), (10,)]


@pytest.mark.parametrize(
    "error",
    [
        "! [rejected] main -> main (non-fast-forward)",
        "! [rejected] main -> main (fetch first)",
        "fatal: Authentication failed",
        "Permission denied (publickey).",
        "fatal: The requested URL returned error: 403",
        "remote: GH013: Repository rule violations found; service unavailable",
        "fatal: an unrecognized permanent failure",
    ],
)
def test_permanent_or_unknown_failure_is_not_retried(monkeypatch, error: str) -> None:
    run = Mock(return_value=_git_result(1, error))
    sleep = Mock()
    monkeypatch.setattr(push_with_retry.subprocess, "run", run)
    monkeypatch.setattr(push_with_retry.time, "sleep", sleep)

    assert push_with_retry.push_with_retry("main") == 1

    run.assert_called_once()
    sleep.assert_not_called()


def test_push_timeout_can_retry_without_rewriting_history(monkeypatch) -> None:
    run = Mock(side_effect=[subprocess.TimeoutExpired(["git"], 60), _git_result(0)])
    monkeypatch.setattr(push_with_retry.subprocess, "run", run)
    monkeypatch.setattr(push_with_retry.time, "sleep", Mock())

    assert push_with_retry.push_with_retry("main") == 0

    assert run.call_count == 2
    assert run.call_args_list[0] == run.call_args_list[1]


def test_remote_url_credentials_are_not_logged(monkeypatch, capsys) -> None:
    error = "fatal: unable to access 'https://token-secret@example.test/repo?token=secret': Permission denied"
    monkeypatch.setattr(
        push_with_retry.subprocess, "run", Mock(return_value=_git_result(128, error))
    )

    assert push_with_retry.push_with_retry("main") == 128

    output = capsys.readouterr().err
    assert "token-secret" not in output
    assert "token=secret" not in output
    assert "Permission denied" in output


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(cwd), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def test_retry_after_remote_accepted_commit_is_idempotent(
    monkeypatch, tmp_path: Path
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    writer = tmp_path / "writer"
    writer.mkdir()
    _git(writer, "init", "--initial-branch=main")
    _git(writer, "config", "user.name", "Test writer")
    _git(writer, "config", "user.email", "writer@example.test")
    _git(writer, "remote", "add", "origin", str(remote))
    (writer / "snapshot.json").write_text('{"public_safe": true}\n', encoding="utf-8")
    _git(writer, "add", "snapshot.json")
    _git(writer, "commit", "-m", "Test snapshot")
    original_head = _git(writer, "rev-parse", "HEAD")
    original_run = subprocess.run
    pushes = []

    def push_with_lost_response(command, **kwargs):
        pushes.append(command)
        result = original_run(command, **kwargs)
        if len(pushes) == 1:
            assert result.returncode == 0
            return _git_result(128, "fatal: connection reset after accepting update")
        return result

    monkeypatch.chdir(writer)
    monkeypatch.setattr(push_with_retry.subprocess, "run", push_with_lost_response)
    monkeypatch.setattr(push_with_retry.time, "sleep", Mock())

    assert push_with_retry.push_with_retry("main") == 0

    assert len(pushes) == 2
    assert pushes[0] == pushes[1]
    assert _git(remote, "rev-parse", "refs/heads/main") == original_head
    assert _git(writer, "rev-parse", "HEAD") == original_head
    assert _git(writer, "status", "--porcelain") == ""
