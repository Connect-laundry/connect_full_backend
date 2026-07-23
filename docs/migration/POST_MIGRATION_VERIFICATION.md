# Post-Migration Verification Report

Run every item after repointing `DATABASE_URL` to Supabase. Check the box only when the
actual result matches. **Do not consider the migration complete until all pass.**

## 1. Data integrity (automated)
- [ ] `python scripts/verify_migration.py --source "$NEON_URL" --target "$SUPABASE_URL"` → **PASS**
      (every table present, every row count identical).
- [ ] Spot-check critical counts match Neon:
      `User`, `Laundry`, `Order`, `Payment`, coupons, referrals, notifications, reviews, analytics.

## 2. Schema & migrations
- [ ] `python manage.py migrate --check` → *No planned migrations* (schema + `django_migrations` carried over).
- [ ] `python manage.py showmigrations` → no `[ ]` unapplied entries.
- [ ] `python manage.py check` → *no issues*.

## 3. Health & resilience
- [ ] `curl .../health/` → `{"status":"healthy"}` (HTTP 200).
- [ ] Admin dashboard loads (no 500).
- [ ] Resilience: with a bad `DATABASE_URL`, an API call returns **503** JSON (not 500) and
      the admin shows the branded 503 page. `tests/test_db_resilience.py` covers this in CI.

## 4. Application flows (functional)
- [ ] **Auth (Clerk):** log in on the mobile app; token verifies; `request.user` resolves.
- [ ] **Orders:** create/list/update an order end-to-end.
- [ ] **Payments (Paystack):** initialize a payment; webhook updates the record.
- [ ] **Laundries:** list, detail, and the admin approval workflow.
- [ ] **Reviews / coupons / referrals:** read + write.
- [ ] **Notifications:** Expo push sends; unread badge + history read correctly.
- [ ] **Images (Cloudinary):** existing image URLs still load; a new upload succeeds
      (storage is unchanged, but confirm URLs referencing DB rows resolve).
- [ ] **Analytics:** dashboard KPIs and charts populate.
- [ ] **Admin:** Orders, Laundries, Users, Sessions changelists open.

## 5. Background jobs
- [ ] Celery workers connect (Redis unchanged) and a task (e.g. OCR / email / campaign) runs.
- [ ] `django_celery_results` writes task results to the **Supabase** DB.

## 6. Test suite
- [ ] `python -m pytest --import-mode=importlib -q` → all pass (tests use the local sqlite
      test DB, so they validate code, not the Supabase data).

## 7. Cut-over hygiene
- [ ] Neon kept intact as rollback target (see `ROLLBACK_GUIDE.md`).
- [ ] `connect_neon_*.sql` dump archived.
- [ ] After a few stable days: downgrade/cancel Neon; optionally give local dev its own
      Supabase project so dev traffic never competes with production.

---

### Row-count snapshot (fill in during migration)

| Table | Neon | Supabase | Match |
|-------|-----:|---------:|:-----:|
| users_user |  |  |  |
| laundries_laundry |  |  |  |
| ordering_order |  |  |  |
| payments_payment |  |  |  |
| ordering_coupon |  |  |  |
| _(add rows as needed)_ |  |  |  |
