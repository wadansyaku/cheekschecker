#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


BIDI_RANGES = (
    (0x202A, 0x202E),
    (0x2066, 0x2069),
)


def contains_bidi_controls(text: str) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    for index, ch in enumerate(text):
        code = ord(ch)
        if any(start <= code <= end for start, end in BIDI_RANGES):
            hits.append((index, code))
    return hits


def validate_retry_timeout(path: Path, lines: list[str]) -> list[str]:
    errors: list[str] = []
    current_step_indent: int | None = None
    has_retry = False
    has_timeout = False
    in_with_block = False
    with_indent: int | None = None

    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(stripped)

        if stripped.startswith("- "):
            if has_retry and not has_timeout:
                errors.append(
                    f"{path}:{line_number} missing timeout_minutes/timeout_seconds for nick-fields/retry step"
                )
            current_step_indent = indent
            has_retry = False
            has_timeout = False
            in_with_block = False
            with_indent = None

        if current_step_indent is None:
            continue

        if indent <= current_step_indent:
            in_with_block = False
            with_indent = None

        if "uses:" in stripped and "nick-fields/retry@" in stripped:
            has_retry = True

        if stripped.startswith("with:"):
            in_with_block = True
            with_indent = indent
            continue

        if in_with_block and with_indent is not None and indent > with_indent:
            if stripped.startswith("timeout_minutes:") or stripped.startswith("timeout_seconds:"):
                has_timeout = True

    if has_retry and not has_timeout:
        errors.append(
            f"{path}:EOF missing timeout_minutes/timeout_seconds for nick-fields/retry step"
        )

    return errors


def main() -> int:
    workflow_dir = Path(".github/workflows")
    workflow_paths = sorted(
        list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))
    )
    if not workflow_paths:
        print("No workflow files found under .github/workflows")
        return 0

    errors: list[str] = []
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        bidi_hits = contains_bidi_controls(text)
        if bidi_hits:
            hits = ", ".join(f"index {index} (U+{code:04X})" for index, code in bidi_hits)
            errors.append(f"{path} contains bidi control characters: {hits}")

        lines = text.splitlines()
        errors.extend(validate_retry_timeout(path, lines))

    if errors:
        print("Workflow validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Workflow validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
