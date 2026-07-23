# Rollback Guide — Supabase → Neon

The migration is designed to be **instantly reversible** because the cut-over is a single
environment variable and **Neon is left intact** (not deleted) until you are confident.

## When to roll back
- `verify_migration.py` reported `FAIL` (row/table mismatch).
- `/health/` stays `degraded` after the cut-over.
- Errors in Sentry/Render logs referencing the database after repointing.

## How to roll back (≈2 minutes)

1. **Restore `DATABASE_URL` to the Neon value** in both places:
   - Render → `connect-full-backend` → Environment → `DATABASE_URL` → paste the Neon URL → Save.
   - Local `.env` → restore the Neon `DATABASE_URL` line.
2. Render redeploys automatically. Confirm:
   ```bash
   curl https://connect-full-backend.onrender.com/health/   # -> {"status":"healthy"}
   ```
3. Neon must be **un-suspended** for this to help — if you rolled back because Supabase
   failed but Neon is still over-quota, upgrade Neon first.

## Important preconditions that make rollback safe
- **Do not delete the Neon project** until production has run on Supabase cleanly for a few days.
- **Keep the `connect_neon_*.sql` dump file** produced by the migration script.
- Avoid writes to Supabase you can't afford to lose during the trial window — any data
  created on Supabase after cut-over will **not** exist in Neon if you roll back. If you must
  roll back after users have written new data, re-dump Supabase and restore into Neon before
  switching (reverse of the migration script).

## No schema divergence
No Django migrations were added as part of this DB move, so Neon and Supabase share the same
schema — rolling back does not require any migration reversal.
