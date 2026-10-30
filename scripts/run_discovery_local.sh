#!/usr/bin/env bash
# Marketplace seller discovery from this machine (Amazon / Myntra block GitHub's servers).
# Installed in crontab every 6 hours:  13 */6 * * *  /home/asta/SmartBuy_AI/scripts/run_discovery_local.sh
# No Brave queries (--no-search): web search runs in the GitHub workflow smartbuy-discover.
# Log: logs/discovery-local.log (last ~5,000 lines kept). flock prevents overlapping runs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/discovery-local.log"
mkdir -p "$ROOT/logs"
exec 9>"$ROOT/logs/.discovery-local.lock"
flock -n 9 || { echo "$(date -Is) previous run still active; skipped" >> "$LOG"; exit 0; }
cd "$ROOT/backend"
{
  echo "=== $(date -Is) start"
  timeout 3600 .venv/bin/python -m app.workers.discovery --no-search && echo "=== $(date -Is) ok" || echo "=== $(date -Is) FAILED ($?)"
} >> "$LOG" 2>&1
tail -n 5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
