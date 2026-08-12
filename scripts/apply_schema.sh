#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and edit it first." >&2
  exit 1
fi

set -a
source .env
set +a

docker compose up -d postgres

echo "Waiting for PostgreSQL..."
for _ in {1..60}; do
  if docker compose exec -T postgres pg_isready \
      -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker compose exec -T postgres pg_isready \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
  echo "PostgreSQL did not become ready. Run: docker compose logs postgres" >&2
  exit 1
fi

docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" < db.sql

echo "Database schema applied successfully."
