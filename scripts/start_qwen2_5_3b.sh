#!/bin/bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
vllm_bin="${VLLM_BIN:-.venv/bin/vllm}"
model_path="${VLLM_MODEL_PATH:-models/Qwen2.5-3B-Instruct}"

if ! command -v "$vllm_bin" >/dev/null; then
  printf 'vLLM executable not found: %s\nSet VLLM_BIN to the installed vLLM executable.\n' "$vllm_bin" >&2
  exit 1
fi

if [[ ! -d "$model_path" ]]; then
  printf 'Local model directory not found: %s\nSet VLLM_MODEL_PATH to the downloaded model directory.\n' "$model_path" >&2
  exit 1
fi

mkdir -p logs
"$vllm_bin" serve "$model_path" \
  --served-model-name "${VLLM_SERVED_MODEL_NAME:-Qwen2.5-3B-Instruct}" \
  --host "${VLLM_HOST:-127.0.0.1}" \
  --port "${VLLM_PORT:-8000}" \
  --dtype "${VLLM_DTYPE:-half}" \
  --max-model-len "${VLLM_MAX_MODEL_LEN:-4096}" \
  --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.75}" \
  "$@" 2>&1 | tee -a logs/qwen2.5-3b.log
