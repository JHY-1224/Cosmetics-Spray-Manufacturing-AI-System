#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
: "${ASSISTANT_MODEL_PYTHON:?指定现有GPU推理环境的Python，不自动安装或升级}"
exec "$ASSISTANT_MODEL_PYTHON" "$SCRIPT_DIR/launch_model.py" text "$@"
