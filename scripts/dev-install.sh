#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${VENV_PYTHON:-$ROOT_DIR/.venv/bin/python}"
DIST_DIR="${DIST_DIR:-$ROOT_DIR/dist}"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Python interpreter not found: $VENV_PYTHON" >&2
  echo "Create the virtualenv first or set VENV_PYTHON." >&2
  exit 1
fi

mkdir -p "$DIST_DIR"
rm -f "$DIST_DIR"/tg_agent_gateway-*.whl

"$VENV_PYTHON" -m pip install --upgrade build
"$VENV_PYTHON" -m build --wheel --outdir "$DIST_DIR"
"$VENV_PYTHON" -m pip install --force-reinstall "$DIST_DIR"/tg_agent_gateway-*.whl
