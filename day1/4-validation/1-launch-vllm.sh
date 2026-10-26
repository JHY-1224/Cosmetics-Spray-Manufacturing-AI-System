#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
: "${COSMETICS_MODEL_DIR:?请指定训练生成的 merged 模型；基座模型需明确选择}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
case "${COSMETICS_MODEL_MODE:-text}" in
  text) set -- --language-model-only ;;
  vision) set -- ;;
  *) echo 'COSMETICS_MODEL_MODE must be text or vision' >&2; exit 2 ;;
esac
exec uv run --project "$ROOT/day2/5-service" vllm serve "$COSMETICS_MODEL_DIR" \
  --host 127.0.0.1 --port "${COSMETICS_MODEL_PORT:-8100}" --max-model-len "${COSMETICS_MAX_MODEL_LEN:-8192}" \
  "$@" --served-model-name "${COSMETICS_SERVED_MODEL_NAME:-cosmetics-qwen35-9b}" \
  --default-chat-template-kwargs '{"enable_thinking": false, "preserve_thinking": true}'
