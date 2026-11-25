#!/bin/bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p logs
python -m uvicorn gateway.main:app \
  --host "${GATEWAY_HOST:-127.0.0.1}" --port "${GATEWAY_PORT:-9000}" \
  2>&1 | tee -a logs/gateway.log
