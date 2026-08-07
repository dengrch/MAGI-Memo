#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    printf 'MAGI virtual environment is missing: %s\n' "$PYTHON_BIN" >&2
    printf 'Run: uv sync --frozen --extra api --extra offline-storage --extra offline-llm\n' >&2
    exit 1
fi

mkdir -p "$ROOT_DIR/mgc-test/inputs" "$ROOT_DIR/mgc-test/ragstore" "$ROOT_DIR/mgc-test/logs"
exec "$PYTHON_BIN" -m magi_core.api.lightrag_server "$@"
