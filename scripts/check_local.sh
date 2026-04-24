#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
  echo "`.venv` が見つかりません。先に scripts/bootstrap_local.sh を実行してください。" >&2
  exit 1
fi

"$VENV_PYTHON" "$ROOT_DIR/scripts/ci/check_workflows.py"
"$VENV_PYTHON" -m mypy "$ROOT_DIR/watch_cheeks.py" "$ROOT_DIR/summarize.py" "$ROOT_DIR/src"
"$VENV_PYTHON" -m pytest "$ROOT_DIR/tests" -v --cov="$ROOT_DIR" --cov-report=term-missing
