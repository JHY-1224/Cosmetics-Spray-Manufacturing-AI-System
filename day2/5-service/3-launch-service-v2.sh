#!/bin/sh
set -eu
cd "$(dirname "$0")"
: "${COSMETICS_MODEL_DIR:?请指定基座模型或训练生成的 merged 模型绝对路径}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec uv run uvicorn service-v2:app --host 127.0.0.1 --port 8101
