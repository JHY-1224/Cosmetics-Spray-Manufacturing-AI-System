#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo '请先按 assistant_app/README.md 准备独立Web环境。'
  exit 1
fi
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/run_assistant.py"
