# Simame Authentication Incident Runbook

Date accessed for source guidance: 2026-08-10.

## Official Sources Used

- Clerk Expo production deployment: https://clerk.com/docs/guides/development/deployment/expo
- Django deployment checklist: https://docs.djangoproject.com/en/dev/howto/deployment/checklist/

## Production Rules

- Production mobile builds must use a production Clerk instance, not `pk_test` keys or `*.clerk.accounts.dev` URLs.
- Clerk Native Applications must include iOS bundle ID, Apple Team ID, Android package, and Android production signing SHA-256.
- Mobile SSO redirect URLs must be allowlisted.
- Django owns authorization decisions; never trust role claims submitted by the mobile client.

## Incident Triage

1. Identify whether impact is login outage, token verification failure, role escalation, session restore failure, or webhook drift.
2. Disable affected privileged access if object-level authorization is suspect.
3. Check Clerk dashboard logs, backend auth logs, and Sentry events using request IDs where available.
4. Verify customer cannot reach owner/admin endpoints and owner A cannot reach owner B resources.
5. Rotate keys only with a coordinated backend/mobile deployment plan.

## Stop Conditions

Stop release if production points to test Clerk, unauthorized role access succeeds, account deletion/logout fails in production, or revoked/expired tokens continue to authorize protected endpoints.