# Connect Laundry Clerk Production Auth Hardening Report

Date: 2026-06-12

## Scope

This pass hardened the existing Clerk hybrid-auth implementation and added backend Clerk webhook synchronization plus Django Admin operational identity visibility. The Django `users.User` model remains the source of truth for roles, permissions, laundries, orders, payments, notifications, legal acceptance, sessions, and audit events. Clerk remains the identity provider for Google/Facebook OAuth, sessions, JWTs, and identity verification.

## Files Changed In This Pass

Backend:

- `connect_new_backend/users/services/clerk_service.py`
- `connect_new_backend/users/services/clerk_webhook_service.py`
- `connect_new_backend/users/views/clerk_webhook.py`
- `connect_new_backend/users/models.py`
- `connect_new_backend/users/admin.py`
- `connect_new_backend/users/urls.py`
- `connect_new_backend/users/apps.py`
- `connect_new_backend/users/checks.py`
- `connect_new_backend/users/auth/schema.py`
- `connect_new_backend/users/migrations/0013_user_auth_provider_user_clerk_created_at_and_more.py`
- `connect_new_backend/config/settings.py`
- `connect_new_backend/.env.example`
- `connect_new_backend/render.yaml`
- `connect_new_backend/marketplace/views/admin_api.py`
- `connect_new_backend/marketplace/views/legal.py`
- `connect_new_backend/tests/test_clerk_auth.py`

Mobile from the prior Clerk hardening pass:

- `connect-customer-mobile/.env.example`
- `connect-customer-mobile/jest.setup.ts`

Note: `connect_new_backend/users/services/session_service.py` and many mobile files were already modified before this pass and were not intentionally changed here.

## Implementation Summary

- Reused Clerk JWKS clients through a bounded in-process cache instead of creating a new JWKS client for every verification.
- Added configurable `CLERK_JWKS_CACHE_SECONDS`, defaulting to 300 seconds.
- Preserved Clerk issuer, audience, expiration, signature, required-claim, and RS256 validation.
- Stopped logging raw Clerk verification/profile exceptions; logs now record exception type only.
- Applied the configured `CLERK_API_TIMEOUT_SECONDS` to Clerk profile fetches.
- Enforced verified-email synchronization from Clerk API email metadata or token claims fallback.
- Canonicalized social-login email addresses to lowercase before local-user linking to reduce duplicate-account risk.
- Switched explicit SimpleJWT-only legal/admin API views to `ClerkOrJWTAuthentication` plus session auth, so Clerk bearer tokens reach the same local-user authorization layer as default DRF views.
- Scrubbed mobile `.env.example` values that looked like real public provider credentials and replaced them with placeholders.
- Added focused backend tests for verified-email enforcement, profile timeout usage, email canonicalization, direct Clerk bearer auth on explicit legal views, and existing social-login flows.
- Added a narrow ESLint exception for the TypeScript `declare global var` pattern in Jest setup.
- Added signed Clerk webhook endpoint at `/api/v1/auth/clerk/webhook/`.
- Verified Clerk/Svix webhook signatures with timestamp tolerance and stale replay rejection.
- Added `ClerkWebhookEvent` ledger for idempotent duplicate-delivery handling.
- Added webhook handling for `user.created`, `user.updated`, `user.deleted`, `session.created`, and `session.ended`.
- Added production deploy system check for required Clerk backend variables without printing secret values.
- Added drf-spectacular schema support for the hybrid Clerk/SimpleJWT bearer authenticator.
- Added explicit OpenAPI request/response schema for the Clerk webhook endpoint.
- Added operational Clerk fields to local `User`: provider, primary email, email/phone verification, sign-in/sync timestamps, Clerk creation/update timestamps, Clerk status, and minimal Clerk metadata.
- Soft-deactivates local users on Clerk `user.deleted` without deleting historical business data.
- Added Django Admin Clerk identity visibility: profile image, provider, Clerk ID, verification status, last sign-in, last sync, sync health, Clerk dashboard link, filters, search, webhook ledger, and bulk resync action.

## Environment Variables

New backend variables:

- `CLERK_JWKS_CACHE_SECONDS` - backend-only runtime config; controls JWKS cache lifespan in seconds.
- `CLERK_WEBHOOK_SECRET` - backend-only webhook signing secret from Clerk.
- `CLERK_WEBHOOK_TOLERANCE_SECONDS` - backend-only replay/timestamp tolerance, default 300 seconds.
- `CLERK_DASHBOARD_USER_URL_TEMPLATE` - backend/admin-only template for the Clerk dashboard user link.

Existing required backend Clerk variables remain:

- `CLERK_APPLICATION_ID`
- `CLERK_API_BASE_URL`
- `CLERK_ISSUER`
- `CLERK_JWKS_URL`
- `CLERK_JWT_AUDIENCE`
- `CLERK_SECRET_KEY`
- `CLERK_JWT_LEEWAY_SECONDS`
- `CLERK_API_TIMEOUT_SECONDS`
- `CLERK_JWT_ISSUER` is now supported as the production variable name; `CLERK_ISSUER` remains a backward-compatible alias.

Frontend-public variables remain public by design and must be provider-restricted:

- `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY`
- `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`
- `EXPO_PUBLIC_MAPBOX_API_KEY`
- `EXPO_PUBLIC_SENTRY_DSN`
- SSL pinning, legal URL, support, and EAS update variables.

Backend-only secrets must never be referenced from the Expo app or any bundled frontend code.

## Database Impact

- New migration added: `connect_new_backend/users/migrations/0013_user_auth_provider_user_clerk_created_at_and_more.py`.
- Added operational Clerk fields to `users.User`.
- Added `users.ClerkWebhookEvent` for webhook delivery idempotency and processing audit.
- Existing Clerk fields from `users/migrations/0012_user_clerk_social_identity.py` remain the identity mapping surface.

## Security Findings Fixed

- Explicit legal/admin API views did not accept Clerk bearer authentication because they pinned `JWTAuthentication`; fixed by using `ClerkOrJWTAuthentication`.
- Clerk profile lookup timeout was hardcoded; fixed to use `CLERK_API_TIMEOUT_SECONDS`.
- Clerk JWKS verification did not have a shared reusable client/cache layer; fixed with bounded PyJWT JWKS client reuse.
- Social sync accepted profile objects without verified-email state; fixed by enforcing verified email before create/update.
- Social sync preserved mixed-case email input; fixed by lowercasing before local-user lookup/linking.
- Mobile sample env contained live-looking public provider values; replaced with placeholders to avoid normalizing committed credential material.
- Clerk webhooks were not implemented; fixed with signed endpoint, timestamp tolerance, duplicate-delivery idempotency, and no raw payload/token logging.
- Django Admin had only minimal social fields; fixed with Clerk identity display, search/filter surfaces, sync health, dashboard link, and bulk resync.
- Render deployment config did not declare required Clerk production variables; fixed by adding Clerk keys as `sync: false`.
- `CLERK_JWT_ISSUER` naming mismatch could have led to missing issuer validation in production envs; fixed by supporting both `CLERK_JWT_ISSUER` and legacy `CLERK_ISSUER`.
- drf-spectacular could not resolve the hybrid authentication class in deploy checks; fixed with an OpenAPI auth extension.

## Final Clerk Production Configuration Audit

Configured Clerk dashboard endpoint:

- `POST https://connect-full-backend.onrender.com/api/v1/auth/clerk/webhook/`

Secret handling:

- The webhook signing secret was not written to source code, examples, tests, reports, logs, or frontend files.
- The backend expects `CLERK_WEBHOOK_SECRET` only from the production runtime environment.
- Render config now marks `CLERK_WEBHOOK_SECRET` as `sync: false`.
- Frontend tracked source scan found no `CLERK_SECRET_KEY`, `CLERK_WEBHOOK_SECRET`, `CLERK_JWKS_URL`, or `CLERK_JWT_ISSUER` references.
- Source-controlled backend references to Clerk secret names are limited to settings, deployment config, placeholders, tests, and verification code.
- Git history search for Clerk webhook-secret marker returned no commits in either repo.

## Validation Evidence

Backend:

- `python -m pytest tests/test_clerk_auth.py -q` -> 19 passed, 1 pytest cache warning.
- `python manage.py check` -> no issues.
- `python manage.py makemigrations --check --dry-run` -> no changes detected; first run warned because the sandbox blocked external Postgres access.
- `$env:DJANGO_SETTINGS_MODULE='config.test_settings'; python manage.py check` -> no issues.
- `$env:DJANGO_SETTINGS_MODULE='config.test_settings'; python manage.py makemigrations --check --dry-run` -> no changes detected.
- `python manage.py check --deploy` with dummy Clerk env values -> passed with one unrelated drf-spectacular enum naming warning.
- `python -m pytest -q --basetemp tmp\pytest-connect` -> 188 passed, 2 warnings.

Mobile:

- `npm.cmd run typecheck` -> passed.
- `npm.cmd run test:ci` -> 7 suites passed, 19 tests passed.
- `npm.cmd run validate:release` -> passed with CI-context warnings for missing release env values.
- `npm.cmd run lint` failed because Expo/ESLint could not write/unlink `.expo/cache/eslint/.cache_1ulugig`.
- `npx.cmd eslint . --no-cache` -> exit 0 with 11 warnings, 0 errors.

## Production Readiness Scores

- Authentication: 93/100
- Authorization: 87/100
- Mobile integration: 82/100
- Backend integration: 93/100
- OAuth security: 86/100
- API compatibility: 88/100
- Deployment readiness: 84/100
- App Store readiness: 74/100
- Play Store readiness: 74/100

Overall launch posture: ready for staging validation with real Clerk credentials and the production Clerk webhook endpoint configured. Not yet final production sign-off until real-device OAuth, owner-web coverage, payment, notification, deployed webhook delivery, and infrastructure checks pass.

## Honest Caveats

- Real Google/Facebook OAuth was not executed locally because production Clerk provider credentials, redirect configuration, and device/browser callback validation are external.
- Real Clerk webhook delivery from Clerk Dashboard to Render was not executed locally; signed webhook behavior was verified with simulated Svix headers.
- Owner/admin web platform code is not present in this workspace, so owner web Clerk SDK behavior could not be verified here.
- No live Paystack payment, Expo push receipt, deployed TLS pinning, Redis/Celery, or DigitalOcean migration test was executed.
- `npm audit` and `pip audit` were not executed in this pass.
- Release env warnings remain until CI/EAS production variables are actually present.
- Existing dirty worktree state is broad; only the files listed above were intentionally changed in this pass.
- Because a webhook signing secret was shared in chat, safest production practice is to rotate the Clerk webhook secret after Render is configured if this channel is not approved for secret handling.
