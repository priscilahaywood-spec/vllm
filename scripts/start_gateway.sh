#!/bin/bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p logs
python -m uvicorn gateway.main:app --host 127.0.0.1 --port 9000 2>&1 | tee -a logs/gateway.log
