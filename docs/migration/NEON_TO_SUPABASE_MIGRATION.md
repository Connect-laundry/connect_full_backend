# Neon → Supabase Migration Guide

**Scope:** Migrate the PostgreSQL database **only**, from Neon to Supabase.
Everything else stays exactly as-is: **Clerk** auth, **Cloudinary** storage, **Redis**,
**Celery**, Expo push, Paystack, Render hosting, and the Django architecture.

**Why:** Neon's free tier meters *compute-hours* and suspends the database when the
monthly allowance is exhausted (100 CU-hours). When suspended, every query errors,
which surfaced as a `Server Error (500)` in the admin dashboard and would break the
mobile apps. Supabase's Postgres does not suspend the same way.

This is a **standard Postgres → Postgres** move — Supabase is plain PostgreSQL, so the
Django ORM needs **no code changes**. The only application change is `DATABASE_URL`.

---

## Pre-flight facts (verified against the codebase)

| Check | Result |
|-------|--------|
| `USE_POSTGIS` | Defaults to **False** — no PostGIS/GIS extension needed |
| Postgres extensions in migrations | **None** (no trigram/citext/hstore/etc.) |
| DB driver | `psycopg` 3 — works with Supabase pooler |
| Migrations present | users 13, laundries 13, ordering 13, payments 5, logistics 1, marketplace 14, analytics 1 |
| Hardcoded Neon refs (app code) | None. Only dev scripts `check_db.py`, `scripts/seed_booking_v2.py` (fallbacks) |

## Connection choice (important)

Supabase offers three connection modes. **Use the Session pooler** for Django on Render:

| Mode | Host / Port | Use it? |
|------|-------------|---------|
| Direct | `db.<ref>.supabase.co:5432` | ❌ IPv6-only; Render can't reach it |
| Transaction pooler | `<region>.pooler.supabase.com:6543` | ❌ Breaks psycopg prepared statements |
| **Session pooler** | `<region>.pooler.supabase.com:5432` | ✅ **Yes** |

Final `DATABASE_URL` (note the appended `?sslmode=require`):

```
postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Use an **alphanumeric password** (no symbols) to avoid URL percent-encoding issues.

---

## Migration procedure

### 0. Make Neon reachable
Neon must respond to queries to export it. Either **briefly upgrade** Neon (fastest —
export, then downgrade), or **wait for the monthly reset**.

### 1. Export + restore + verify (one script)
```bash
export NEON_URL='postgresql://neondb_owner:PASS@ep-round-haze-afaxzo0c-pooler.c-2.us-west-2.aws.neon.tech/neondb?sslmode=require'
export SUPABASE_URL='postgresql://postgres.<ref>:PASS@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require'
bash scripts/migrate_neon_to_supabase.sh
```
The script runs `pg_dump` (read-only on Neon, `--no-owner --no-privileges`), restores
into Supabase with `psql`, then runs `scripts/verify_migration.py` to compare row counts
table-by-table. **Do not proceed unless verification reports `PASS`.**

### 2. Repoint the application
Update `DATABASE_URL` in **both** places to the Supabase Session pooler URL:
- **Render** → `connect-full-backend` service → Environment → `DATABASE_URL` → Save (auto-redeploys).
- **Local** → `connect_new_backend/.env`.

The Dockerfile `CMD` runs `migrate --noinput` on boot; because the dump already carries
the full schema **and** `django_migrations`, this should report **no pending migrations**.

### 3. Confirm
```bash
python manage.py migrate --check                # -> no planned migrations
curl https://connect-full-backend.onrender.com/health/   # -> {"status":"healthy"}
```
Because all users came across, **existing admin logins still work** — no `createsuperuser`.

See `POST_MIGRATION_VERIFICATION.md` for the full acceptance checklist and
`ROLLBACK_GUIDE.md` if anything looks wrong.

---

## What was added for resilience (already merged on this branch)

So a database outage never again shows a raw 500:
- `config/resilience.py` — builds a structured **503** (JSON for apps, HTML page for admin).
- `config/middleware/database_availability.py` — catches DB errors during view processing.
- `config/middleware/deactivation.py` — guards the pre-view session lookup (the exact path
  that produced the admin HTML 500).
- `config/exception_handler.py` — DRF API responses return 503 (with `Retry-After`) on DB errors.
- `templates/503.html` — branded "temporarily unavailable" page.
- `settings.py` — `connect_timeout=10s` so a dead DB fails fast instead of hanging.

Covered by `tests/test_db_resilience.py`.
