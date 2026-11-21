#!/bin/bash
set -euo pipefail

export CUDA_HOME=/root/cuda-toolkit-13.2
export CUDA_PATH="$CUDA_HOME"
export PATH="$CUDA_HOME/bin:$PATH"
export FLASHINFER_NVCC="$CUDA_HOME/bin/nvcc"
export LIBRARY_PATH="$CUDA_HOME/lib:$CUDA_HOME/lib/stubs:${LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="$CUDA_HOME/lib:${LD_LIBRARY_PATH:-}"

unset VLLM_USE_FLASHINFER_SAMPLER

cd /root/llm-inference-optimization
mkdir -p logs

/root/miniconda3/envs/vllm/bin/vllm serve \
  /root/llm-inference-optimization/models/Qwen3-8B \
  --served-model-name Qwen3-8B \
  --host 127.0.0.1 \
  --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  2>&1 | tee -a logs/qwen3-8b.log
