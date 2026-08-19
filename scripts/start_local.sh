#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  echo "The virtual environment is missing. Run: make setup" >&2
  exit 1
fi

set -a
source .env
set +a

docker compose up -d postgres

# Do not rely on whichever environment happened to be active in the caller's
# shell.  Calling the project interpreter explicitly prevents a different
# environment (for example ~/pytorch_env) from supplying Streamlit or psycopg.
if ! .venv/bin/python -c "import psycopg, psycopg_binary, streamlit"; then
  echo "ScholarRadar's Python dependencies are incomplete. Run: make setup" >&2
  exit 1
fi

worker_pid=""
cleanup() {
  if [[ -n "$worker_pid" ]] && kill -0 "$worker_pid" 2>/dev/null; then
    kill "$worker_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

.venv/bin/python -m scripts.run_worker \
  --poll-seconds "${RADAR_WORKER_POLL_SECONDS:-3}" \
  > /tmp/scholarradar-worker.log 2>&1 &
worker_pid=$!
echo "ScholarRadar worker started (PID $worker_pid; log: /tmp/scholarradar-worker.log)"

.venv/bin/python -m streamlit run app.py
