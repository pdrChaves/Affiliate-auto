#!/bin/bash
# $1 port, $2 db
cd /home/claude/promo-radar
DATABASE_PATH=$2 PORT=$1 DISABLE_SCHEDULER=${3:-1} CATALOG_MODE=mock setsid nohup python3 -m app.cli serve > /home/claude/qa/run/server_$1.log 2>&1 &
for i in $(seq 1 50); do curl -s localhost:$1/health >/dev/null && break; sleep 0.2; done
echo started $1
