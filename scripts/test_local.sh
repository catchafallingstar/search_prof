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
source .venv/bin/activate

python -m unittest discover -s tests -v
python -m scripts.smoke_test_db
python -m scripts.smoke_test_streamlit

echo "All local tests passed."
