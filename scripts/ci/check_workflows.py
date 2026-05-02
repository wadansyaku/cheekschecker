#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


BIDI_RANGES = (
    (0x202A, 0x202E),
    (0x2066, 0x2069),
)

MANUAL_DIAGNOSTIC_ARTIFACTS = {
    "monitor.yml": {
        "sanitized-table": "fetched_table_sanitized.html",
        "masked-history": "history_masked.json",
        "monitor-state": "monitor_state.json",
    },
    "summary_weekly.yml": {
        "weekly-summary-raw": "weekly_summary_raw.json",
    },
    "summary_monthly.yml": {
        "monthly-summary-raw": "monthly_summary_raw.json",
    },
}

WRITER_COMMIT_TARGETS = {
    "monitor.yml": "git add monitor_state.json history_masked.json",
    "summary_weekly.yml": "git add monitor_state.json history_masked.json summary_masked.json",
    "summary_monthly.yml": "git add monitor_state.json history_masked.json summary_masked.json",
}

SUMMARY_CONTRACTS = {
    "summary_weekly.yml": {
        "period": "weekly",
        "days": "7",
        "raw_output": "weekly_summary_raw.json",
    },
    "summary_monthly.yml": {
        "period": "monthly",
        "days": "30",
        "raw_output": "monthly_summary_raw.json",
    },
}

NODE24_OPT_IN = "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: 'true'"
LEGACY_NODE20_ACTION_REFS = (
    "actions/checkout@v4",
    "actions/setup-python@v5",
    "actions/cache@v4",
    "actions/upload-artifact@v4",
    "nick-fields/retry@v3",
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


def _contains_line(lines: list[str], needle: str) -> bool:
    return any(needle in line for line in lines)


def _uses_javascript_action(lines: list[str]) -> bool:
    javascript_action_prefixes = (
        "actions/",
        "nick-fields/retry@",
    )
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- uses:"):
            action_ref = stripped.split("- uses:", 1)[1].strip()
        elif stripped.startswith("uses:"):
            action_ref = stripped.split("uses:", 1)[1].strip()
        else:
            continue
        if action_ref.startswith(javascript_action_prefixes):
            return True
    return False


def _action_refs(lines: list[str]) -> list[str]:
    refs: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- uses:"):
            refs.append(stripped.split("- uses:", 1)[1].strip())
        elif stripped.startswith("uses:"):
            refs.append(stripped.split("uses:", 1)[1].strip())
    return refs


def _workflow_step_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    current_start: int | None = None
    current: list[str] = []

    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("- name:"):
            if current_start is not None:
                blocks.append((current_start, current))
            current_start = line_number
            current = [line]
            continue
        if current_start is not None:
            current.append(line)

    if current_start is not None:
        blocks.append((current_start, current))
    return blocks


def _find_step_block(
    blocks: list[tuple[int, list[str]]],
    needle: str,
) -> tuple[int, list[str]] | None:
    for start, block in blocks:
        if needle in "\n".join(block):
            return start, block
    return None


def _find_artifact_block(
    blocks: list[tuple[int, list[str]]],
    artifact_name: str,
) -> tuple[int, list[str]] | None:
    for start, block in blocks:
        step_text = "\n".join(block)
        if "actions/upload-artifact" not in step_text:
            continue
        if f"name: {artifact_name}" in step_text:
            return start, block
    return None


def _step_has_line(block: list[str], expected: str) -> bool:
    return any(line.strip() == expected for line in block)


def _block_contains(block: list[str], needle: str) -> bool:
    return any(needle in line for line in block)


def _validate_manual_artifact_contract(
    path: Path,
    blocks: list[tuple[int, list[str]]],
) -> list[str]:
    errors: list[str] = []
    contracts = MANUAL_DIAGNOSTIC_ARTIFACTS.get(path.name, {})
    for artifact_name, artifact_path in contracts.items():
        found = _find_artifact_block(blocks, artifact_name)
        if found is None:
            errors.append(f"{path} missing manual diagnostic artifact {artifact_name}")
            continue

        start, block = found
        if not _step_has_line(block, "if: github.event_name == 'workflow_dispatch'"):
            errors.append(
                f"{path}:{start} artifact {artifact_name} must be workflow_dispatch-only"
            )
        if not _step_has_line(block, "retention-days: 3"):
            errors.append(f"{path}:{start} artifact {artifact_name} must use retention-days: 3")
        if not _step_has_line(block, "if-no-files-found: error"):
            errors.append(f"{path}:{start} artifact {artifact_name} must fail when missing")
        if not _step_has_line(block, f"path: {artifact_path}"):
            errors.append(f"{path}:{start} artifact {artifact_name} must upload {artifact_path}")
    return errors


def _validate_allow_fetch_failure_contract(
    path: Path,
    blocks: list[tuple[int, list[str]]],
) -> list[str]:
    errors: list[str] = []
    if path.name == "monitor.yml":
        scheduled = _find_step_block(blocks, "Run monitor (scheduled)")
        manual = _find_step_block(blocks, "Run monitor with sanitized artifact")
        if scheduled is None or not _step_has_line(scheduled[1], "if: github.event_name == 'schedule'"):
            errors.append(f"{path} scheduled monitor must be gated to github.event_name == 'schedule'")
        if scheduled is None or not _step_has_line(scheduled[1], "ALLOW_FETCH_FAILURE: '1'"):
            errors.append(f"{path} scheduled monitor must set ALLOW_FETCH_FAILURE: '1'")
        if manual is None or not _step_has_line(manual[1], "if: github.event_name == 'workflow_dispatch'"):
            errors.append(f"{path} manual monitor must be gated to workflow_dispatch")
        if manual is None or not _step_has_line(manual[1], "ALLOW_FETCH_FAILURE: '0'"):
            errors.append(f"{path} manual monitor must set ALLOW_FETCH_FAILURE: '0'")
    elif path.name in {"summary_weekly.yml", "summary_monthly.yml"}:
        collect = _find_step_block(blocks, "Collect ")
        expected = "ALLOW_FETCH_FAILURE: ${{ github.event_name == 'schedule' && '1' || '0' }}"
        if collect is None or not _step_has_line(collect[1], expected):
            errors.append(
                f"{path} summary collection must set ALLOW_FETCH_FAILURE to schedule-only graceful mode"
            )
    return errors


def _validate_monitor_dispatch_input_contract(path: Path, lines: list[str]) -> list[str]:
    if path.name != "monitor.yml":
        return []

    required = (
        "send_monitor_diagnostic:",
        "description: 'Send a synthetic public-safe monitor Slack notification'",
        "required: false",
        "default: false",
        "type: boolean",
    )
    missing = [line for line in required if not _contains_line(lines, line)]
    if missing:
        return [f"{path} monitor diagnostic workflow_dispatch input is missing: {', '.join(missing)}"]
    return []


def _validate_monitor_slack_diagnostic_contract(
    path: Path,
    blocks: list[tuple[int, list[str]]],
) -> list[str]:
    if path.name != "monitor.yml":
        return []

    errors: list[str] = []
    diagnostic = _find_step_block(blocks, "Send monitor Slack diagnostic")
    if diagnostic is None:
        return [f"{path} manual monitor must include a Slack diagnostic step"]

    start, block = diagnostic
    if not _step_has_line(block, "if: github.event_name == 'workflow_dispatch' && inputs.send_monitor_diagnostic"):
        errors.append(f"{path}:{start} monitor Slack diagnostic must be manual-input gated")
    if not _step_has_line(block, "SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}"):
        errors.append(f"{path}:{start} monitor Slack diagnostic must use SLACK_WEBHOOK_URL secret")
    if not _step_has_line(block, "run: python watch_cheeks.py monitor-diagnostic"):
        errors.append(f"{path}:{start} monitor Slack diagnostic must run monitor-diagnostic")
    return errors


def _validate_summary_contract(
    path: Path,
    blocks: list[tuple[int, list[str]]],
) -> list[str]:
    contract = SUMMARY_CONTRACTS.get(path.name)
    if contract is None:
        return []

    errors: list[str] = []
    period = contract["period"]
    days = contract["days"]
    raw_output = contract["raw_output"]

    ping = _find_step_block(blocks, "--ping-only")
    if ping is None:
        errors.append(f"{path} manual summary workflow must include a Slack webhook ping")
    else:
        start, block = ping
        if not _step_has_line(block, "if: github.event_name == 'workflow_dispatch'"):
            errors.append(f"{path}:{start} summary Slack ping must be workflow_dispatch-only")
        if not _step_has_line(block, "SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}"):
            errors.append(f"{path}:{start} summary Slack ping must use SLACK_WEBHOOK_URL secret")
        if not _step_has_line(block, f"run: python summarize.py --period {period} --ping-only"):
            errors.append(f"{path}:{start} summary Slack ping must use period {period}")

    collect = _find_step_block(blocks, f"Collect {period} dataset")
    if collect is None:
        errors.append(f"{path} summary workflow must collect the {period} dataset")
    else:
        start, block = collect
        expected_run = (
            f"run: python watch_cheeks.py summary --days {days} "
            f"--raw-output {raw_output} --no-notify"
        )
        if not _step_has_line(block, expected_run):
            errors.append(f"{path}:{start} summary collection must write {raw_output} without notifying")

    notify = _find_step_block(blocks, f"Generate {period} summary & notify")
    if notify is None:
        errors.append(f"{path} summary workflow must generate and notify the {period} summary")
    else:
        start, block = notify
        if not _step_has_line(block, "SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}"):
            errors.append(f"{path}:{start} summary notify must use SLACK_WEBHOOK_URL secret")
        expected_run = (
            f"run: python summarize.py --period {period} --raw-data {raw_output} "
            "--history history_masked.json --output summary_masked.json"
        )
        if not _step_has_line(block, expected_run):
            errors.append(f"{path}:{start} summary notify must consume {raw_output}")

    return errors


def _validate_notify_failure_contract(
    path: Path,
    text: str,
    blocks: list[tuple[int, list[str]]],
) -> list[str]:
    if "notify-failure:" not in text:
        return []

    notify_failure_section = text.split("notify-failure:", 1)[1]
    errors: list[str] = []
    if "contents: write" in notify_failure_section:
        errors.append(f"{path} notify-failure job must not request contents: write")

    notify_step = _find_step_block(blocks, "Notify Slack on failure")
    if notify_step is None:
        errors.append(f"{path} notify-failure job must include a Slack notification step")
        return errors

    start, block = notify_step
    if not _step_has_line(block, "SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}"):
        errors.append(f"{path}:{start} notify-failure must use SLACK_WEBHOOK_URL secret")
    if not _block_contains(block, 'if [ -z "$SLACK_WEBHOOK_URL" ]; then'):
        errors.append(f"{path}:{start} notify-failure must skip cleanly when Slack webhook is unset")
    if not _block_contains(block, 'curl --fail-with-body --show-error --silent -X POST "$SLACK_WEBHOOK_URL"'):
        errors.append(f"{path}:{start} notify-failure curl must fail on Slack HTTP errors")
    if not _block_contains(block, "json.dumps(payload, ensure_ascii=False)"):
        errors.append(f"{path}:{start} notify-failure must JSON-encode the Slack payload")
    if not _block_contains(block, "--data-binary @slack_failure_payload.json"):
        errors.append(f"{path}:{start} notify-failure must post a generated Slack payload file")
    block_text = "\n".join(block)
    if '-d "{' in block_text or "--data '{" in block_text:
        errors.append(f"{path}:{start} notify-failure must not inline raw JSON in shell")
    has_run_url_env = _block_contains(
        block,
        "RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}",
    )
    uses_run_url = _block_contains(block, 'run_url = os.environ.get("RUN_URL", "")') and _block_contains(
        block,
        '"url": run_url',
    )
    if not has_run_url_env or not uses_run_url:
        errors.append(f"{path}:{start} notify-failure must link to the current workflow run")
    has_event_env = _block_contains(block, "EVENT_NAME: ${{ github.event_name }}")
    uses_event_name = _block_contains(
        block,
        'event_name = os.environ.get("EVENT_NAME", "")',
    ) and _block_contains(block, "*Trigger:* {event_name}")
    if not has_event_env or not uses_event_name:
        errors.append(f"{path}:{start} notify-failure must include the trigger in Slack output")
    return errors


def validate_public_safe_workflow_contract(path: Path, lines: list[str]) -> list[str]:
    errors: list[str] = []
    text = "\n".join(lines)
    path_name = path.name
    is_writer = path_name in {"monitor.yml", "summary_weekly.yml", "summary_monthly.yml"}
    blocks = _workflow_step_blocks(lines)

    if _uses_javascript_action(lines) and not _contains_line(lines, NODE24_OPT_IN):
        errors.append(f"{path} must opt JavaScript actions into Node 24 with {NODE24_OPT_IN}")

    for action_ref in _action_refs(lines):
        if action_ref in LEGACY_NODE20_ACTION_REFS:
            errors.append(f"{path} must not use legacy Node 20 action {action_ref}")

    if "git push || true" in text or "git push origin" in text and "|| true" in text:
        errors.append(f"{path} must not ignore git push failures")

    if is_writer:
        if not _contains_line(lines, "group: public-safe-state-writer"):
            errors.append(f"{path} must use public-safe-state-writer concurrency group")
        if not _contains_line(lines, "contents: write"):
            errors.append(f"{path} writer job must declare contents: write")
        if not _contains_line(lines, "git pull --rebase --autostash"):
            errors.append(f"{path} writer job must sync before writing public-safe artifacts")
        if not _contains_line(lines, "TZ: Asia/Tokyo"):
            errors.append(f"{path} writer workflow must run with TZ: Asia/Tokyo")
        if not _contains_line(lines, "ROBOTS_ENFORCE: '1'"):
            errors.append(f"{path} writer workflow must enforce robots.txt")
        if path_name == "monitor.yml" and not _contains_line(lines, "WARNING_THROTTLE_MINUTES: '180'"):
            errors.append(f"{path} scheduled monitor must define WARNING_THROTTLE_MINUTES: '180'")
        expected_add = WRITER_COMMIT_TARGETS[path_name]
        if not _contains_line(lines, expected_add):
            errors.append(f"{path} writer workflow must commit exactly: {expected_add}")
        if not _contains_line(lines, "git push origin HEAD:${{ github.ref_name }}"):
            errors.append(f"{path} writer workflow must push to the current ref with git push origin HEAD:${{ github.ref_name }}")
        errors.extend(_validate_monitor_dispatch_input_contract(path, lines))
        errors.extend(_validate_manual_artifact_contract(path, blocks))
        errors.extend(_validate_allow_fetch_failure_contract(path, blocks))
        errors.extend(_validate_monitor_slack_diagnostic_contract(path, blocks))
        errors.extend(_validate_summary_contract(path, blocks))

    errors.extend(_validate_notify_failure_contract(path, text, blocks))

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
        errors.extend(validate_public_safe_workflow_contract(path, lines))

    if errors:
        print("Workflow validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Workflow validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
