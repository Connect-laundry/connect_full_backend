# Environment Variable Checklist

Only **one** variable changes for this migration: `DATABASE_URL`. Everything else stays.

## Changed

| Variable | Before (Neon) | After (Supabase) |
|----------|---------------|------------------|
| `DATABASE_URL` | `postgresql://neondb_owner:…@ep-…neon.tech/neondb?sslmode=require` | `postgresql://postgres.<ref>:<PWD>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require` |

Set it in **both**: Render (`connect-full-backend` service) **and** local `.env`.

## Optional (new, has safe defaults — only set to override)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_CONNECT_TIMEOUT` | `10` | Seconds before a dead DB fails fast → quick 503 instead of a hung worker |
| `CONN_MAX_AGE` | `0` prod / `60` dev | Persistent connection lifetime (leave as-is for the Supabase pooler) |

## Unchanged — do NOT touch (keep exactly as they are)

- **Clerk:** `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`, `CLERK_JWT_ISSUER`,
  `CLERK_JWT_AUDIENCE`, `CLERK_WEBHOOK_SECRET`, `CLERK_APPLICATION_ID`
- **Cloudinary:** `CLOUDINARY_URL` / `CLOUDINARY_*` (storage stays on Cloudinary)
- **Redis / Celery:** `REDIS_URL`, `CELERY_BROKER_URL`, `USE_REDIS_CACHE`
- **Paystack:** `PAYSTACK_SECRET_KEY`
- **Expo push, Sentry, SMTP, `SECRET_KEY`, `ALLOWED_HOSTS`, `FRONTEND_URL`**, etc.

## Supabase keys — NOT needed for this migration

The Supabase dashboard shows `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
`SUPABASE_JWKS_URL`. These are for the Supabase client SDK / Storage / Auth — **none are
required** here because Django connects to Supabase purely as a PostgreSQL database via
`DATABASE_URL`. Do not add them yet (they'd only be needed if you later move Storage or
Realtime to Supabase, which is out of scope).

## Post-change verification
```bash
python manage.py check
python manage.py migrate --check          # -> "No planned migrations"
curl https://connect-full-backend.onrender.com/health/
```
