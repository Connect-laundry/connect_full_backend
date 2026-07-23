#!/usr/bin/env bash
#
# Neon -> Supabase PostgreSQL migration (data-preserving).
#
# Prerequisites:
#   * Neon is REACHABLE (briefly upgraded, or after monthly quota reset).
#   * Supabase project created; you have the SESSION POOLER connection string.
#   * PostgreSQL 16+ client tools (this repo has v18 at the path below).
#
# Usage:
#   export NEON_URL='postgresql://neondb_owner:PASS@ep-...neon.tech/neondb?sslmode=require'
#   export SUPABASE_URL='postgresql://postgres.REF:PASS@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require'
#   bash scripts/migrate_neon_to_supabase.sh
#
# The script is idempotent for the dump step (re-dumps to a timestamped file) and
# SAFE: it never touches Neon except to read (pg_dump). Review output before you
# repoint DATABASE_URL.
set -euo pipefail

PG_BIN="${PG_BIN:-/c/Program Files/PostgreSQL/18/bin}"
PGDUMP="$PG_BIN/pg_dump.exe"
PSQL="$PG_BIN/psql.exe"
STAMP="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${DUMP_FILE:-connect_neon_${STAMP}.sql}"

: "${NEON_URL:?Set NEON_URL to the Neon connection string}"
: "${SUPABASE_URL:?Set SUPABASE_URL to the Supabase SESSION POOLER connection string}"

echo "==> [1/4] Dumping Neon -> ${DUMP_FILE}"
# --no-owner/--no-privileges: strip Neon-specific roles so it restores cleanly as
# the Supabase 'postgres' user. Plain SQL keeps schema + data + sequences +
# django_migrations together.
"$PGDUMP" "$NEON_URL" \
    --no-owner --no-privileges --no-acl \
    --format=plain \
    -f "$DUMP_FILE"
echo "    dump size: $(wc -c < "$DUMP_FILE") bytes"

echo "==> [2/4] Restoring into Supabase"
# ON_ERROR_STOP=0 tolerates benign "schema public already exists" notices; scan
# the output for any real table/data errors.
"$PSQL" "$SUPABASE_URL" -v ON_ERROR_STOP=0 -f "$DUMP_FILE"

echo "==> [3/4] Running row-count verification (source vs target)"
python scripts/verify_migration.py --source "$NEON_URL" --target "$SUPABASE_URL"

echo "==> [4/4] Done."
echo "    If verification PASSED:"
echo "      1. Update DATABASE_URL in Render (connect-full-backend) and local .env to \$SUPABASE_URL"
echo "      2. Redeploy; the Dockerfile runs 'migrate' (should report nothing pending)"
echo "      3. curl https://connect-full-backend.onrender.com/health/  -> {\"status\":\"healthy\"}"
echo "    Keep ${DUMP_FILE} as a backup until you've confirmed production is stable."
