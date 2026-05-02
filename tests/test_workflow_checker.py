from pathlib import Path

from scripts.ci.check_workflows import validate_public_safe_workflow_contract


def _notify_failure_step(workflow_name: str) -> list[str]:
    return [
        "  notify-failure:",
        "    permissions:",
        "      contents: read",
        "    steps:",
        "      - name: Notify Slack on failure",
        "        env:",
        "          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}",
        f"          WORKFLOW_NAME: {workflow_name}",
        "          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}",
        "          REF_NAME: ${{ github.ref_name }}",
        "          EVENT_NAME: ${{ github.event_name }}",
        "        run: |",
        '          if [ -z "$SLACK_WEBHOOK_URL" ]; then',
        '            echo "SLACK_WEBHOOK_URL is not set; skipping failure notification"',
        "            exit 0",
        "          fi",
        "          python - <<'PY' > slack_failure_payload.json",
        "          import json",
        "          import os",
        '          event_name = os.environ.get("EVENT_NAME", "")',
        '          run_url = os.environ.get("RUN_URL", "")',
        '          payload = {"text": f"Workflow Failed: *Trigger:* {event_name}", "blocks": [{"type": "actions", "elements": [{"url": run_url}]}]}',
        "          print(json.dumps(payload, ensure_ascii=False))",
        "          PY",
        '          if ! curl --fail-with-body --show-error --silent -X POST "$SLACK_WEBHOOK_URL" \\',
        "            -H 'Content-Type: application/json' \\",
        "            --data-binary @slack_failure_payload.json; then",
        '            echo "Slack failure notification failed" >&2',
        "          fi",
    ]


def _summary_weekly_workflow_lines(
    *,
    artifact_if: str = "github.event_name == 'workflow_dispatch'",
    missing_files: str = "error",
    retention: str = "3",
) -> list[str]:
    return [
        "permissions:",
        "  contents: read",
        "env:",
        "  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: 'true'",
        "  TZ: Asia/Tokyo",
        "  ROBOTS_ENFORCE: '1'",
        "concurrency:",
        "  group: public-safe-state-writer",
        "jobs:",
        "  summary:",
        "    permissions:",
        "      contents: write",
        "    steps:",
        "      - name: Sync masked history",
        "        run: git pull --rebase --autostash origin main",
        "      - name: Webhook疎通テスト",
        "        if: github.event_name == 'workflow_dispatch'",
        "        env:",
        "          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}",
        "        run: python summarize.py --period weekly --ping-only",
        "      - name: Collect weekly dataset",
        "        env:",
        "          ALLOW_FETCH_FAILURE: ${{ github.event_name == 'schedule' && '1' || '0' }}",
        "        run: python watch_cheeks.py summary --days 7 --raw-output weekly_summary_raw.json --no-notify",
        "      - name: Upload raw weekly summary",
        f"        if: {artifact_if}",
        "        uses: actions/upload-artifact@v7",
        "        with:",
        "          name: weekly-summary-raw",
        "          path: weekly_summary_raw.json",
        f"          if-no-files-found: {missing_files}",
        f"          retention-days: {retention}",
        "      - name: Generate weekly summary & notify",
        "        env:",
        "          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}",
        "        run: python summarize.py --period weekly --raw-data weekly_summary_raw.json --history history_masked.json --output summary_masked.json",
        "      - name: Commit public-safe archives",
        "        run: |",
        "          git add monitor_state.json history_masked.json summary_masked.json",
        "          git push origin HEAD:${{ github.ref_name }}",
    ] + _notify_failure_step("Weekly Summary")


def _monitor_workflow_lines(
    *,
    missing_files: str = "error",
    retention: str = "3",
    scheduled_if: str = "github.event_name == 'schedule'",
) -> list[str]:
    return [
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
        "      send_monitor_diagnostic:",
        "        description: 'Send a synthetic public-safe monitor Slack notification'",
        "        required: false",
        "        default: false",
        "        type: boolean",
        "permissions:",
        "  contents: read",
        "env:",
        "  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: 'true'",
        "  TZ: Asia/Tokyo",
        "  ROBOTS_ENFORCE: '1'",
        "  WARNING_THROTTLE_MINUTES: '180'",
        "concurrency:",
        "  group: public-safe-state-writer",
        "jobs:",
        "  monitor:",
        "    permissions:",
        "      contents: write",
        "    steps:",
        "      - name: Sync public-safe artifacts",
        "        run: git pull --rebase --autostash origin main",
        "      - name: Run monitor (scheduled)",
        f"        if: {scheduled_if}",
        "        env:",
        "          ALLOW_FETCH_FAILURE: '1'",
        "        run: python watch_cheeks.py monitor",
        "      - name: Run monitor with sanitized artifact",
        "        if: github.event_name == 'workflow_dispatch'",
        "        env:",
        "          ALLOW_FETCH_FAILURE: '0'",
        "        run: python watch_cheeks.py monitor --sanitized-output fetched_table_sanitized.html",
        "      - name: Upload sanitized table",
        "        if: github.event_name == 'workflow_dispatch'",
        "        uses: actions/upload-artifact@v7",
        "        with:",
        "          name: sanitized-table",
        "          path: fetched_table_sanitized.html",
        f"          if-no-files-found: {missing_files}",
        f"          retention-days: {retention}",
        "      - name: Upload masked history snapshot",
        "        if: github.event_name == 'workflow_dispatch'",
        "        uses: actions/upload-artifact@v7",
        "        with:",
        "          name: masked-history",
        "          path: history_masked.json",
        f"          if-no-files-found: {missing_files}",
        "          retention-days: 3",
        "      - name: Upload monitor state snapshot",
        "        if: github.event_name == 'workflow_dispatch'",
        "        uses: actions/upload-artifact@v7",
        "        with:",
        "          name: monitor-state",
        "          path: monitor_state.json",
        f"          if-no-files-found: {missing_files}",
        "          retention-days: 3",
        "      - name: Send monitor Slack diagnostic",
        "        if: github.event_name == 'workflow_dispatch' && inputs.send_monitor_diagnostic",
        "        env:",
        "          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}",
        "        run: python watch_cheeks.py monitor-diagnostic",
        "      - name: Commit public-safe monitor artifacts",
        "        run: |",
        "          git add monitor_state.json history_masked.json",
        "          git push origin HEAD:${{ github.ref_name }}",
    ] + _notify_failure_step("Monitor Calendar")


def test_summary_raw_artifact_must_be_manual_only() -> None:
    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/summary_weekly.yml"),
        _summary_weekly_workflow_lines(artifact_if="always()"),
    )

    assert any("workflow_dispatch-only" in error for error in errors)


def test_writer_workflow_contract_accepts_manual_raw_artifact() -> None:
    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/summary_weekly.yml"),
        _summary_weekly_workflow_lines(),
    )

    assert errors == []


def test_manual_artifact_retention_is_fixed() -> None:
    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/monitor.yml"),
        _monitor_workflow_lines(retention="30"),
    )

    assert any("sanitized-table must use retention-days: 3" in error for error in errors)


def test_manual_artifact_must_fail_when_missing() -> None:
    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/monitor.yml"),
        _monitor_workflow_lines(missing_files="ignore"),
    )

    assert any("sanitized-table must fail when missing" in error for error in errors)


def test_monitor_artifact_contract_accepts_expected_shape() -> None:
    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/monitor.yml"),
        _monitor_workflow_lines(),
    )

    assert errors == []


def test_monitor_scheduled_step_must_be_schedule_only() -> None:
    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/monitor.yml"),
        _monitor_workflow_lines(scheduled_if="github.event_name != 'workflow_dispatch'"),
    )

    assert any("scheduled monitor must be gated" in error for error in errors)


def test_monitor_requires_manual_slack_diagnostic_step() -> None:
    lines = [
        line
        for line in _monitor_workflow_lines()
        if "Send monitor Slack diagnostic" not in line
        and "inputs.send_monitor_diagnostic" not in line
        and "SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}" not in line
        and "run: python watch_cheeks.py monitor-diagnostic" not in line
    ]

    errors = validate_public_safe_workflow_contract(Path(".github/workflows/monitor.yml"), lines)

    assert any("Slack diagnostic step" in error for error in errors)


def test_summary_requires_manual_webhook_ping() -> None:
    lines = [
        line
        for line in _summary_weekly_workflow_lines()
        if "--ping-only" not in line and "Webhook疎通テスト" not in line
    ]

    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/summary_weekly.yml"),
        lines,
    )

    assert any("Slack webhook ping" in error for error in errors)


def test_summary_collection_must_not_notify_directly() -> None:
    lines = [
        line.replace(" --no-notify", "") if "watch_cheeks.py summary" in line else line
        for line in _summary_weekly_workflow_lines()
    ]

    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/summary_weekly.yml"),
        lines,
    )

    assert any("without notifying" in error for error in errors)


def test_notify_failure_curl_must_fail_on_slack_http_errors() -> None:
    lines = [
        line.replace("--fail-with-body --show-error --silent ", "")
        for line in _monitor_workflow_lines()
    ]

    errors = validate_public_safe_workflow_contract(Path(".github/workflows/monitor.yml"), lines)

    assert any("curl must fail on Slack HTTP errors" in error for error in errors)


def test_writer_push_target_must_use_current_ref() -> None:
    lines = [
        line.replace("git push origin HEAD:${{ github.ref_name }}", "git push origin main")
        for line in _summary_weekly_workflow_lines()
    ]

    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/summary_weekly.yml"),
        lines,
    )

    assert any("push to the current ref" in error for error in errors)


def test_workflows_using_javascript_actions_require_node24_opt_in() -> None:
    lines = [
        "name: Tests",
        "jobs:",
        "  test:",
        "    steps:",
        "      - uses: actions/checkout@v6",
    ]

    errors = validate_public_safe_workflow_contract(Path(".github/workflows/test.yml"), lines)

    assert any("Node 24" in error for error in errors)


def test_legacy_node20_action_refs_are_rejected() -> None:
    lines = [
        "env:",
        "  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: 'true'",
        "jobs:",
        "  test:",
        "    steps:",
        "      - uses: actions/checkout@v4",
    ]

    errors = validate_public_safe_workflow_contract(Path(".github/workflows/test.yml"), lines)

    assert any("legacy Node 20 action actions/checkout@v4" in error for error in errors)
