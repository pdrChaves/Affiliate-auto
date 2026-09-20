#!/bin/bash
# uso: qa/start.sh PORTA CAMINHO_DO_BANCO [DISABLE_SCHEDULER=1]
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
mkdir -p qa/run
DATABASE_PATH=$2 PORT=$1 DISABLE_SCHEDULER=${3:-1} CATALOG_MODE=mock PANEL_USER=admin \
PANEL_PASSWORD=${QA_PASSWORD:-senha-de-teste-forte-123} LOGIN_MAX_FAILURES=${QA_MAX_FAILURES:-5} \
  setsid nohup python3 -m app.cli serve > qa/run/server_$1.log 2>&1 &
for i in $(seq 1 50); do curl -s localhost:$1/health >/dev/null && break; sleep 0.2; done
echo started $1
