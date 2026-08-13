# Simame Rollback Runbook

Date accessed for source guidance: 2026-08-10.

## Rollback Types

- Backend rollback: redeploy previous backend commit after checking migration compatibility.
- Database rollback: restore to non-production first; production restore requires explicit business approval because it can lose data.
- Mobile binary rollback: use App Store / Play phased rollout controls where available; otherwise ship a fixed binary.
- OTA rollback: use EAS Update rollback only for JavaScript/assets compatible with the same runtime version.

## OTA Safety

Safe OTA candidates include JavaScript, UI, and business logic that does not require native changes. New binary required for native modules, permissions, entitlements, Info.plist, Android Manifest, plugins, native SDK changes, bundle/package identifiers, and signing changes.

## Procedure

1. Declare incident owner and affected environments.
2. Stop further promotion or rollout.
3. Decide rollback type based on changed surface.
4. Confirm database migration reversibility before backend rollback.
5. Execute rollback in staging first where feasible.
6. Verify health/readiness, auth, order creation, payment verification, push notification route, and owner order view.
7. Monitor Sentry and logs after rollback.

## Stop Conditions

Do not roll back across irreversible migrations without a data recovery plan. Do not publish a production OTA that targets the wrong channel or runtime.