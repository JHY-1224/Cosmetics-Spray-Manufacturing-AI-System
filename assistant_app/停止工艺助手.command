#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/stop_assistant.py"
