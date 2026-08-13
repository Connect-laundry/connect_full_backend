# Simame Database Recovery Runbook

Date accessed for source guidance: 2026-08-10.

## Official Sources Used

- Supabase database overview: https://supabase.com/docs/guides/database/overview
- Supabase Postgres connection guidance: https://supabase.com/docs/guides/database/connecting-to-postgres
- Django deployment checklist: https://docs.djangoproject.com/en/dev/howto/deployment/checklist/

## Current Project Signals

`connect_new_backend/config/settings.py` uses `DATABASE_URL` through `dj_database_url`, enables SSL outside DEBUG, sets connection health checks, and disables server-side cursors outside DEBUG. `render.yaml` expects `DATABASE_URL` across backend service definitions.

## Connection Rules

- Application traffic: use the host-appropriate pooled connection mode after confirming whether the deployment network supports IPv6.
- Migrations, `pg_dump`, backup, restore, and replication: use a direct database connection, not the same pooler URL blindly.
- Production connections must require SSL.

## Backup Policy

- RPO target: define before launch; recommended starting target is 24 hours or less.
- RTO target: define before launch; recommended starting target is 4 hours or less.
- Keep at least one off-provider encrypted backup.
- Test restore into a non-production database before production launch.

## Restore Drill

1. Create or select a non-production restore database.
2. Restore the latest production backup into that target.
3. Run migrations/checks against the restored target.
4. Run smoke queries for users, laundries, orders, payments, notifications, and audit logs.
5. Record backup timestamp, restore duration, data checks, and owner.

## Stop Conditions

Production is NO-GO if there is no tested restore, unknown migration drift, no backup owner, or no documented RPO/RTO accepted by the business.