# Simame Production Deployment Runbook

Date accessed for source guidance: 2026-08-10.

## Scope

This runbook covers the Simame backend, customer Expo app, and owner web app. It does not authorize an App Store, Google Play, EAS Update, database, or dashboard change by itself. Every production action requires human approval from Philip.

## Official Sources Used

- Expo app config: https://docs.expo.dev/versions/v56.0.0/config/app/
- Expo EAS environment variables: https://docs.expo.dev/eas/environment-variables/
- Expo runtime versions and updates: https://docs.expo.dev/eas-update/runtime-versions/
- Django deployment checklist: https://docs.djangoproject.com/en/dev/howto/deployment/checklist/
- Supabase Postgres connection guidance: https://supabase.com/docs/guides/database/connecting-to-postgres
- Expo/Sentry setup: https://docs.expo.dev/guides/using-sentry/

## Release Order

1. Freeze the release candidate branch and record commit SHAs for `connect_new_backend`, `connect-customer-mobile`, and `Connect-Web-App`.
2. Confirm production database backup and restore target exist.
3. Run backend gates from `connect_new_backend`: `python manage.py check`, `python manage.py check --deploy`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --plan`, `python manage.py spectacular --file docs/api/simame-openapi.yaml --validate`, and `python -m pytest`.
4. Run customer mobile gates from `connect-customer-mobile`: `npm.cmd run typecheck`, `npm.cmd run lint`, `npm.cmd run test:ci`, `npm.cmd run validate:release:strict`, and `npx.cmd expo-doctor`.
5. Run owner web gates from `Connect-Web-App`: `npm.cmd run lint`, test/build scripts that exist in `package.json`, and a browser smoke test against staging.
6. Deploy backend-compatible changes before mobile binaries that depend on them.
7. Apply database migrations only after backup confirmation and migration plan review.
8. Verify `/health/` and add or verify `/readiness/` before traffic cutover.
9. Build and distribute production-signed mobile binaries through TestFlight and Google Play testing tracks.
10. Monitor Sentry, backend logs, payment reconciliation, notification delivery, and order creation for at least 24 hours after release.

## Current Release Gate

NO-GO until the production environment matrix is corrected, Clerk production keys are configured, production-signed builds are verified on physical devices, backup restore is tested, Paystack live webhooks are verified, and monitoring evidence exists.