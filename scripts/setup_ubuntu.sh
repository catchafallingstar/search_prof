#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

for command_name in python3 docker; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is missing: $command_name" >&2
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required (the 'docker compose' command)." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit its email, name, and password, then rerun setup." >&2
  exit 1
fi

if grep -Eq 'REPLACE_WITH|you@example\.com' .env; then
  echo "Edit .env and replace the example email/name values before setup." >&2
  exit 1
fi

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

bash scripts/apply_schema.sh
python -m scripts.bootstrap_owner
bash scripts/test_local.sh

echo
echo "Setup complete. Start the site with: make start"
