#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

set -a
source .env
set +a

backup_dir="$PROJECT_DIR/backups"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
backup_path="$backup_dir/scholarradar-$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose up -d postgres >/dev/null
umask 077
docker compose exec -T postgres pg_dump \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --format custom \
  --no-owner \
  --no-acl > "$backup_path"

echo "Private database backup created: $backup_path"
echo "Test restoration periodically; a backup is useful only when it can be restored."
