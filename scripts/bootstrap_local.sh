#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

"$PYTHON_BIN" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ が必要です。PYTHON_BIN を指定するか、python3.11 を利用してください。")
PY

if [ ! -x "$VENV_PYTHON" ]; then
  "$PYTHON_BIN" -m venv "$ROOT_DIR/.venv"
fi

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r "$ROOT_DIR/requirements.txt" -r "$ROOT_DIR/requirements-dev.txt"

if [ "${SKIP_PLAYWRIGHT_INSTALL:-0}" != "1" ]; then
  "$VENV_PYTHON" -m playwright install chromium
fi

cat <<EOF
Bootstrap completed.

Next steps:
  source "$ROOT_DIR/.venv/bin/activate"
  scripts/check_local.sh
EOF
