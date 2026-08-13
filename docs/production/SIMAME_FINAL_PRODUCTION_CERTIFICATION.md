# Simame Final Pre-Production Certification

Audit date: 2026-08-13

## Executive Verdict

# NO-GO

Simame is **not ready for a production store submission or public release**. The audited source now passes broad backend, mobile and owner-web checks, and the definite P0/P1 code defects found in account deletion, order mutation, payment-state parity, payout visibility and owner financial state were fixed. The verdict remains NO-GO because production identity/configuration is invalid or incomplete, the live API reports degraded health, infrastructure changes are local only, no production Paystack charge/refund has been proven, no backup restore has been rehearsed, and no signed Android/iOS artifact has passed physical-device acceptance.

No deployment, credential rotation, production build, OTA publish, store submission, real charge/refund or destructive restore was performed.

## P0 Blockers

1. **Production Clerk is not configured.** Every EAS profile still contains a `pk_test_` publishable key and development Clerk URLs. Production login and OAuth cannot be certified.
2. **Strict EAS validation fails.** Fifteen required names are missing from the audit environment, plus the configured EAS project ID is not proven as a real UUID, two certificate pins are not present, and pin expiry is not at least 45 days away. See `SIMAME_MANUAL_PRODUCTION_ACTIONS.md`.
3. **The live backend is degraded.** A read-only probe on 2026-08-13 returned HTTP 200 with `{"status":"degraded"}` from `/health/`. `/live/` and `/readiness/` return 404 because the local health changes have not been deployed.
4. **Previously exposed credentials require human rotation.** The Expo access token and Clerk webhook signing secret must be revoked/replaced in their dashboards and downstream environments.
5. **Paystack live behavior is unproven.** Code and tests verify ownership, exact reference, metadata, amount, currency, domain, HMAC signature, idempotency and reconciliation, but no real charge/refund or live webhook delivery was performed. Paystack states that callback arrival is not proof of payment and server verification/webhooks must determine truth: [accept payments](https://paystack.com/docs/payments/accept-payments/), [verify payments](https://paystack.com/docs/payments/verify-payments/), [webhook signature](https://paystack.com/docs/payments/webhooks/).
6. **No signed production artifacts were inspected.** Package/bundle IDs, signing, resolved API 36, merged permissions, production env, deep links and entitlements remain unproven in a real AAB/IPA.
7. **Store compliance is incomplete.** The external deletion page, final privacy/terms URLs, Google Data Safety, Apple App Privacy, and signed-artifact permission disclosures require owner/legal/store action.
8. **Recovery is unproven.** Backup scripts/runbooks exist, but there is no measured restore rehearsal, RTO, RPO or validated restored database.
9. **Current hosting plans/config are not proven production-safe.** The local Render Blueprint now requests persistent paid plans and `/readiness/`, but no remote plan/billing/deploy action occurred.

## P1 Blockers

1. All EAS profiles currently resolve to the same production Render API; a genuinely isolated staging backend is not evidenced. Preview testing can affect production data.
2. Sentry code/config hooks exist, but production organization/project credentials, source maps, alert routing, PII scrubbing and release dashboard evidence are incomplete. Local exports warned that Sentry organization/project config was missing.
3. Push code and token lifecycle are implemented, but production Expo credential rotation, APNs/FCM delivery and physical-device deep-link behavior remain unverified.
4. Production Django security settings were not available locally. `check --deploy` ran against local debug settings and warned about DEBUG, HTTPS redirect, HSTS and secure cookies. The deployed environment must be checked with its actual environment values.

## P2 Improvements

- Mobile lint: 49 warnings, 0 errors, mainly unused imports and hook dependencies.
- Owner web lint: 181 warnings, 0 errors, mainly `any`, console use and one React compiler warning.
- OpenAPI: 0 errors, 3 enum naming warnings.
- Django settings contain duplicated Celery configuration blocks; consolidate without changing runtime behavior.
- `app/settings/payment.tsx` is an orphaned Payment Methods placeholder. No saved-card backend/PCI product exists; keep it unreachable or remove it after product approval.
- Expo web export fails because `react-native-maps` imports native internals. Android and iOS exports pass; web is not the customer release target.
- The deprecated `/booking/*` route alias should get a telemetry-backed retirement date. Owner web now uses canonical `/orders/*` lifecycle routes.

## Backend <-> Mobile Parity Summary

Source of truth: `MOBILE_BACKEND_PARITY_MATRIX.md`.

- Validated OpenAPI paths: **174**.
- Documented paths that do not resolve: **0** after normalized path parameters.
- Actual useful routes absent from OpenAPI: **3** Paystack callback/webhook operations; router roots and format suffixes are also resolver-only.
- Customer scope: **84 paths / 95 HTTP operations** including compatibility aliases.
- Directly integrated/reachable paths: **63**.
- Compatibility/granular aliases with no separate UI required: **21**.
- Required customer backend-only gaps after fixes: **0**.
- Partially integrated required customer features after fixes: **0**.
- Broken customer paths found after fixes: **0**.
- Physical end-to-end workflows tested on signed devices: **0**; this remains an external release blocker.

Code defects fixed:

- Order responses/mobile normalization now preserve payment reference, provider status and payment method.
- Mobile displays pending, failed, expired, refund pending, refunded, cash due and awaiting-invoice states with state-correct actions.
- Generic order PUT/PATCH/DELETE was removed; only explicit lifecycle transitions can mutate state.
- Account deletion now deletes the Clerk identity first, fails closed if Clerk cannot be reached, revokes sessions, removes direct profile/device/address/reset data and transactionally anonymizes the local user while retaining legally necessary records.
- Payment verification rejects a foreign reference before calling Paystack.

Frontend-only finding: the unreachable payment-method placeholder has no backend saved-payment-method capability.

## Owner Web Parity Summary

Owner pages cover onboarding/business profile, service/pricing models, price imports, schedules, pricing versions/rollback, delivery zones, holiday hours, vacation mode, orders/lifecycle, assignments/staff, notifications, earnings and account/session controls.

Definite parity defects fixed:

- The hours-template service incorrectly used GET against a backend POST action and was unreachable. It is now a visible Apply defaults action with loading/error handling and a route regression test.
- The backend payout endpoint was not exposed. Earnings now shows authoritative held/available/paid balances, recent settlement rows and payout history.
- Cancelled/rejected orders were incorrectly fabricated as `COMPLETED` transactions. Only delivered/completed orders enter completed-order history; payout truth comes from the settlement endpoint.
- Owner lifecycle/timeline/price-breakdown calls now use canonical `/orders/*` routes.
- Protected-route middleware now includes notifications, business and machines paths.

Owner verification: clean install succeeded, dependency audit 0, 3 test files/5 tests pass, TypeScript passes, production build passes, lint has 0 errors/181 warnings.

## Authentication Readiness

**NO-GO externally; code path improved.** Mobile supports signup, login, OAuth sync, refresh, logout, session list/revocation, password reset and deletion. Backend Clerk webhook synchronization and deletion share the same anonymization service. [Clerk documents Backend API/user deletion](https://clerk.com/docs/guides/users/managing).

Still required: production Clerk instance/domain/keys, rotated webhook secret, native Android signing SHA-256, iOS Team/bundle registration, production OAuth credentials/redirect allowlist and physical-device validation. No Clerk secret may enter an Expo public variable.

## Payment Readiness

**Test/code readiness: strong. Live readiness: NO-GO.** Initialization is server-side; no Paystack secret exists in mobile. Backend amount/currency/reference/metadata/domain/ownership checks, Decimal-safe pesewa conversion, exact-payment behavior, webhook HMAC, idempotency, reconciliation, refund states and lifecycle gates are covered by tests. Mobile now preserves and renders all internal provider states and resumes the existing pending authorization session rather than silently overwriting it.

Paystack's documented provider statuses include abandoned, failed, ongoing, pending, processing, reversed and success; Simame maps these to its explicit internal pending/failure/expired/refund/success states and obtains final truth through verify/webhook. A real live-mode checklist is in `PHYSICAL_DEVICE_ACCEPTANCE_TEST.md`.

## Privacy / Store Compliance Readiness

Engineering inventory is complete in `STORE_DATA_DISCLOSURE_MATRIX.md`; legal/business attestation is not. Name, contact details, identifiers, address/location, transaction history, profile images, push tokens, analytics, diagnostics, reviews and feedback have been traced to backend/SDK processors.

Unused microphone access was removed (`expo-av` removed; image-picker microphone permission disabled). Final artifact permissions and third-party dashboard retention/PII settings still require manual confirmation.

## Android API 36 Readiness

- Expo resolved SDK: **54.0.0**.
- Package: `com.connectlaundry.app` (stable identifier intentionally unchanged).
- Runtime policy: fingerprint.
- Expo SDK 54's documented Android compile/target SDK is **36**, satisfying the Google Play Android 16/API 36 requirement in source configuration: [Expo SDK reference](https://docs.expo.dev/versions/v55.0.0/), [Google Play target API requirement](https://support.google.com/googleplay/android-developer/answer/11926878).
- Android-only Expo export passed and produced a 9.45 MB Hermes bundle.

**Artifact status: pending.** The final signed AAB must be inspected in bundle explorer to prove target SDK, merged manifest, signing and production environment. Therefore API 36 is a source-config PASS, not a final store-artifact PASS.

## Dependency Security Status

- Backend after removal of unused vulnerable `ecdsa`: `pip-audit` reports no known vulnerabilities.
- Owner web: `npm audit` reports 0.
- Mobile: 26 advisories (10 high, 16 moderate, 0 critical); production omit-dev count is 25. High entries trace to Expo/Metro `image-size` build tooling, not a remotely callable app runtime. No compatible supported fix was offered.
- Expo install validation passes and Expo Doctor is 18/18.

Detailed reachability, fix constraints and accepted controls are in `MOBILE_DEPENDENCY_SECURITY_ASSESSMENT.md`. No forced upgrade was performed.

## Backup/Restore Status

**NO-GO until rehearsal.** `DATABASE_RECOVERY.md` and the exact manual drill in `SIMAME_MANUAL_PRODUCTION_ACTIONS.md` define backup, isolated restore, validation, reconnection and rollback. Current plan/frequency/retention/encryption/RTO/RPO and measured restore evidence are not confirmed. Render warns that free databases are not suitable production durability; local Blueprint plan changes require owner approval and deployment: [Render free limitations](https://render.com/docs/free), [Blueprint specification](https://render.com/docs/blueprint-spec).

## Monitoring Status

Local code provides redacted logging, Sentry hooks, payment/webhook error paths and safe health payloads. `/live/` is dependency-free; `/readiness/` checks required dependencies without exposing secrets. Render health-check guidance supports a dedicated endpoint: [Render health checks](https://render.com/docs/health-checks).

Current production evidence is insufficient: `/health/` is degraded, new endpoints are undeployed, and dashboard alert/source-map/worker/beat evidence is missing.

## Physical Device Testing Status

**NOT RUN.** `PHYSICAL_DEVICE_ACCEPTANCE_TEST.md` covers clean install, identity/deletion, permissions, location, booking, every payment state, owner/customer synchronization, push/deep links, poor network, background/kill/restart, Android/iOS and old/new OS coverage. Expo Go is not accepted as Android SDK 53+ push proof.

## Store Readiness

**NO-GO.** No signed AAB/IPA, production Clerk identity, complete strict EAS environment, external deletion page, privacy forms, device sign-off, live payment proof or store-console review exists. No store submission was attempted.

## Remaining Manual Actions

The ordered owner checklist, exact dashboards, value types, verification and skip risks are in `SIMAME_MANUAL_PRODUCTION_ACTIONS.md`. Highest priority:

1. Rotate Expo and Clerk webhook credentials.
2. Configure Clerk production/native OAuth identities.
3. Populate all strict EAS production values and eliminate test Clerk values.
4. Diagnose live degraded health; approve/deploy Render plan/readiness changes.
5. Configure Paystack live webhook/keys and run controlled live charge/refund.
6. Rehearse isolated database restore and measure RTO/RPO.
7. Publish deletion/privacy/terms pages and complete store disclosures.
8. Build/inspect signed artifacts and execute physical-device acceptance.

## Exact Commands Run

Backend:

```text
python manage.py spectacular --file docs/api/simame-openapi.yaml --validate
python manage.py check
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py migrate --check
python -m pytest -q
python -m pytest tests/test_forgot_password.py -q
pip-audit -r requirements.txt
```

Mobile:

```text
npm.cmd uninstall expo-av --ignore-scripts
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run test:ci -- --runInBand
npm.cmd run validate:release
npm.cmd run validate:release:strict
npm.cmd audit --json
npm.cmd audit --omit=dev --json
npm.cmd explain image-size
npm.cmd explain uuid
npx.cmd expo install --check
npx.cmd expo-doctor
npx.cmd expo config --type introspect --json
npx.cmd expo export --platform android
npx.cmd expo export --platform ios
npx.cmd expo export --platform all
```

Owner web:

```text
npm.cmd ci --ignore-scripts
npm.cmd audit --package-lock-only
npm.cmd run lint
npx.cmd tsc --noEmit
npm.cmd test
npm.cmd run build
```

Live read-only probe:

```text
Invoke-WebRequest https://connect-full-backend.onrender.com/health/
Invoke-WebRequest https://connect-full-backend.onrender.com/live/
Invoke-WebRequest https://connect-full-backend.onrender.com/readiness/
```

## Test Results

| Surface | Result |
|---|---|
| Backend full suite, final | **710 passed, 1 skipped**, 14 deprecation warnings |
| Backend focused account deletion/Clerk | **5 passed** |
| Backend focused COD/order/payment suites | **49 passed, 1 skipped**; the skip is the PostgreSQL row-lock concurrency case under SQLite |
| Django normal check | **0 issues** |
| Django deploy check in local debug env | **7 warnings**, including debug/HTTPS/cookies and schema enum warnings |
| Migration drift / pending migrations | **No model drift**; `0018_order_payment_method` and `0012_payment_amount_collected_payment_collected_by_and_more` are pending deployment |
| OpenAPI | **0 errors**, 3 warnings |
| Mobile Jest | **31 suites, 269 tests passed** |
| Mobile TypeScript | **pass** |
| Mobile lint | **0 errors, 49 warnings** |
| Expo Doctor | **18/18 passed** |
| Android export | **pass**, 72 files / 21.56 MB total, 9.45 MB Hermes bundle |
| iOS export | **pass**, 71 files / 21.55 MB total, 9.44 MB Hermes bundle |
| All-platform export | Native bundles completed; web failed on native-only `react-native-maps` |
| Strict release validation | **failed as expected**, 15 missing names plus 3 derived constraints |
| Owner tests | **4 files, 8 tests passed** |
| Owner TypeScript/build | **pass / pass** |
| Owner lint | **0 errors, 181 warnings** |
| Owner dependency audit | **0 vulnerabilities** |
| Live backend health | HTTP 200 **degraded**; live/readiness 404 before deployment |

## Changed Files

Key backend changes:

- `users/services/account_deletion.py`, `users/services/clerk_service.py`, `users/views/profile.py`
- `ordering/serializers/order.py`, `ordering/views/order_views.py`, `ordering/signals.py`
- `payments/views.py`, payment/security tests
- `config/views/health.py`, `config/urls.py`, `config/settings.py`
- `render.yaml`, `requirements.txt`
- account deletion, order/payment, health and branding tests
- Simame customer-facing backend/email/admin/report strings
- regenerated `docs/api/simame-openapi.yaml`

Key mobile changes:

- `app.config.ts`, `package.json`, `package-lock.json`
- splash/onboarding/home/brand and documentation branding
- `src/features/orders/utils/paymentState.ts`
- order types/normalizer/receipt/payment screens
- payment-state and normalization tests

Key owner-web changes:

- Simame metadata/login/landing/header/manifest branding
- `src/proxy.ts`
- hours-template API/UI plus regression test
- payout/settlement earnings API/UI plus financial-state regression tests
- canonical owner order routes

Required evidence created:

- `MOBILE_BACKEND_PARITY_MATRIX.md`
- `MOBILE_DEPENDENCY_SECURITY_ASSESSMENT.md`
- `STORE_DATA_DISCLOSURE_MATRIX.md`
- `PHYSICAL_DEVICE_ACCEPTANCE_TEST.md`
- `SIMAME_MANUAL_PRODUCTION_ACTIONS.md`
- `SIMAME_FINAL_PRODUCTION_CERTIFICATION.md`

The worktrees were already dirty and remain uncommitted. No unrelated work was reverted.

## Deferred Risks

- Production environment and external dashboards cannot be proven from local source.
- Live Paystack behavior can differ from test/provider fixtures.
- Mobile deep links, push, OAuth, SSL pinning, integrity checks and permissions require signed physical artifacts.
- Database recovery remains theoretical until an isolated restore succeeds.
- Mobile Expo/Metro advisories remain tracked upstream under documented build-host controls.
- Production plan cost/billing and remote Blueprint application require owner approval.
- Financial retention, privacy disclosures and deletion-policy wording require business/legal approval.

## Final Release Recommendation

Do **not** submit to TestFlight, Play closed testing or production yet. First complete P0 manual configuration/rotation, restore live health, deploy and verify readiness, pass strict EAS validation, inspect signed artifacts, run controlled Paystack live tests and execute the physical-device plan. Re-run this certification against the exact release commit and production environment; only then consider changing NO-GO.

## Approved COD / Custom-Quote Certification

> Simame supports Cash on Delivery. Cash/COD orders may be accepted by an owner before payment and remain financially unpaid until cash is actually collected. Custom-quote orders may also be accepted before payment. Paystack/online-payment controls remain strict and separate.

The prior unpaid-owner-acceptance P1 ambiguity is resolved in source:

- COD is explicit on Order; a missing Payment row is never interpreted as COD.
- COD booking and acceptance create no fake payment, provider reference, settlement, payout balance or paid earnings.
- Owner/admin cash collection is server-authoritative, exact-amount, fulfillment-state constrained, audited, transactional and idempotent.
- Ordinary unpaid online orders remain blocked from acceptance even when no Payment row exists.
- Custom quote is a pricing mode, not a payment method. It can be accepted before payment and follows COD or Paystack rules after pricing.
- Paid owner revenue requires PAID payment status; collected cash is reported separately from Paystack payout balances.
- COD completion requires confirmed cash collection. Unpaid COD cancellation has no refund; post-collection cash refunds require a separately approved manual product policy.

This policy fix does not change the overall **NO-GO** verdict or resolve the independent production blockers above.
