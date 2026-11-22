#!/bin/sh
# Starts (or restarts) the dev copy of Pipkin on port 8766, next to the real app on 8765.
# Rerun this after the agent changes server.py or agent.py. For dist/ changes, just reload the page.
cd "$(dirname "$0")" || exit 1
pkill -f "server.py --serve --port 8766" 2>/dev/null && sleep 1
/usr/bin/python3 -B server.py --serve --port 8766 >/dev/null 2>&1 &
for _ in $(seq 1 50); do
  curl -s -m 1 http://127.0.0.1:8766/health >/dev/null 2>&1 && break
  sleep 0.2
done
open "http://127.0.0.1:8766"
echo "Pipkin Dev is running at http://127.0.0.1:8766"
echo "It stops by itself a few seconds after you close its tab. You can close this window."
