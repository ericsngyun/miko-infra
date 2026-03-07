#!/bin/bash
# ============================================================================
# Run all migrations against master-postgres in order
# Usage: ./run_migrations.sh [postgres_host] [postgres_port] [postgres_password]
# Defaults: localhost 5432 (reads password from SOPS if not provided)
# ============================================================================
set -euo pipefail

DB_HOST="${1:-localhost}"
DB_PORT="${2:-5432}"
DB_NAME="awaas_master"
DB_USER="awaas"
DB_PASS="${3:-${MASTER_POSTGRES_PASSWORD:-}}"

if [[ -z "$DB_PASS" ]]; then
  echo "ERROR: Postgres password required. Pass as arg3 or set MASTER_POSTGRES_PASSWORD"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MIGRATION_DIR="$SCRIPT_DIR"

echo "=== Running AWaaS migrations against $DB_HOST:$DB_PORT/$DB_NAME ==="

export PGPASSWORD="$DB_PASS"

for sql_file in "$MIGRATION_DIR"/0*.sql; do
  filename=$(basename "$sql_file")
  echo "  -> $filename"
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$sql_file" -v ON_ERROR_STOP=1
  echo "     OK"
done

# Run seed after migrations
if [[ -f "$MIGRATION_DIR/seed_objectives.sql" ]]; then
  echo "  -> seed_objectives.sql"
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$MIGRATION_DIR/seed_objectives.sql" -v ON_ERROR_STOP=1
  echo "     OK"
fi

echo "=== All migrations complete ==="
unset PGPASSWORD
