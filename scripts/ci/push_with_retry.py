#!/usr/bin/env python3
"""Retry a committed writer snapshot only after transient transport failures."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

MAX_PUSH_ATTEMPTS = 3
PUSH_TIMEOUT_SECONDS = 60
RETRY_DELAY_SECONDS = 5

_PERMANENT_ERRORS = (
    "non-fast-forward",
    "fetch first",
    "permission denied",
    "authentication failed",
    "access denied",
    "repository not found",
    "protected branch",
    "pre-receive hook declined",
    "hook declined",
    "gh006",
    "gh013",
    "error: 401",
    "error: 403",
    "could not read username",
)
_TRANSIENT_ERRORS = (
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "connection reset",
    "connection closed",
    "the remote end hung up unexpectedly",
    "remote end hung up unexpectedly",
    "empty reply from server",
    "send failure",
    "recv failure",
)


def _is_transient_failure(output: str) -> bool:
    lowered = output.lower()
    if any(marker in lowered for marker in _PERMANENT_ERRORS):
        return False
    return any(marker in lowered for marker in _TRANSIENT_ERRORS) or bool(
        re.search(r"(?:http[^\n]*|error:\s*)\b50[0-4]\b", lowered)
    )


def _print_git_output(output: str) -> None:
    # Remote URLs can contain credentials; retain the reason without the URL.
    safe_output = re.sub(r"https?://[^\s<>\"']+", "[remote URL]", output).strip()
    if safe_output:
        print(safe_output, file=sys.stderr)


def push_with_retry(ref: str) -> int:
    """Push the same HEAD at most three times without rewriting local history."""
    command = ["git", "push", "--porcelain", "origin", f"HEAD:refs/heads/{ref}"]
    for attempt in range(1, MAX_PUSH_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=PUSH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            returncode = 1
            retryable = True
            print("Git push exceeded its time limit.", file=sys.stderr)
        else:
            if result.returncode == 0:
                print(f"Writer snapshot push accepted (attempt {attempt}).")
                return 0
            returncode = result.returncode
            output = f"{result.stdout}\n{result.stderr}"
            retryable = _is_transient_failure(output)
            _print_git_output(output)

        if not retryable or attempt == MAX_PUSH_ATTEMPTS:
            print(
                f"Writer snapshot push failed after {attempt} attempt(s).",
                file=sys.stderr,
            )
            return returncode if returncode > 0 else 1
        delay = RETRY_DELAY_SECONDS * attempt
        print(
            f"Transient push failure; retrying the same commit in {delay}s "
            f"({attempt + 1}/{MAX_PUSH_ATTEMPTS}).",
            file=sys.stderr,
        )
        time.sleep(delay)
    return 1  # pragma: no cover - the bounded loop always returns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref", help="Current writer branch name")
    return push_with_retry(parser.parse_args().ref)


if __name__ == "__main__":
    sys.exit(main())
