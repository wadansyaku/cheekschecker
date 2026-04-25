from pathlib import Path

from scripts.ci.check_workflows import validate_public_safe_workflow_contract


def _summary_weekly_workflow_lines(
    *,
    artifact_if: str = "github.event_name == 'workflow_dispatch'",
    retention: str = "3",
) -> list[str]:
    return [
        "env:",
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
        "      - name: Collect weekly dataset",
        "        env:",
        "          ALLOW_FETCH_FAILURE: ${{ github.event_name == 'schedule' && '1' || '0' }}",
        "        run: python watch_cheeks.py summary --days 7 --raw-output weekly_summary_raw.json --no-notify",
        "      - name: Upload raw weekly summary",
        f"        if: {artifact_if}",
        "        uses: actions/upload-artifact@v4",
        "        with:",
        "          name: weekly-summary-raw",
        "          path: weekly_summary_raw.json",
        f"          retention-days: {retention}",
        "      - name: Commit public-safe archives",
        "        run: |",
        "          git add monitor_state.json history_masked.json summary_masked.json",
        "  notify-failure:",
        "    permissions:",
        "      contents: read",
    ]


def _monitor_workflow_lines(*, retention: str = "3") -> list[str]:
    return [
        "env:",
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
        "        env:",
        "          ALLOW_FETCH_FAILURE: '1'",
        "        run: python watch_cheeks.py monitor",
        "      - name: Run monitor with sanitized artifact",
        "        env:",
        "          ALLOW_FETCH_FAILURE: '0'",
        "        run: python watch_cheeks.py monitor --sanitized-output fetched_table_sanitized.html",
        "      - name: Upload sanitized table",
        "        if: github.event_name == 'workflow_dispatch'",
        "        uses: actions/upload-artifact@v4",
        "        with:",
        "          name: sanitized-table",
        "          path: fetched_table_sanitized.html",
        f"          retention-days: {retention}",
        "      - name: Upload masked history snapshot",
        "        if: github.event_name == 'workflow_dispatch'",
        "        uses: actions/upload-artifact@v4",
        "        with:",
        "          name: masked-history",
        "          path: history_masked.json",
        "          retention-days: 3",
        "      - name: Upload monitor state snapshot",
        "        if: github.event_name == 'workflow_dispatch'",
        "        uses: actions/upload-artifact@v4",
        "        with:",
        "          name: monitor-state",
        "          path: monitor_state.json",
        "          retention-days: 3",
        "      - name: Send monitor Slack diagnostic",
        "        if: github.event_name == 'workflow_dispatch' && inputs.send_monitor_diagnostic",
        "        env:",
        "          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}",
        "        run: python watch_cheeks.py monitor-diagnostic",
        "      - name: Commit public-safe monitor artifacts",
        "        run: |",
        "          git add monitor_state.json history_masked.json",
        "  notify-failure:",
        "    permissions:",
        "      contents: read",
    ]


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


def test_monitor_artifact_contract_accepts_expected_shape() -> None:
    errors = validate_public_safe_workflow_contract(
        Path(".github/workflows/monitor.yml"),
        _monitor_workflow_lines(),
    )

    assert errors == []


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
