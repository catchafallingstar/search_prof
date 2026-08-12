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
source .venv/bin/activate
exec streamlit run app.py
