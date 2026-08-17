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

exec .venv/bin/python -m streamlit run app.py
