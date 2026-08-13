# Simame Production Certification Report

Date accessed for official source research: 2026-08-13.

## Verdict

NO-GO

Simame is not production-certified from the evidence available in this workspace. The mobile splash/app-facing name has been changed to Simame, and production runbooks now exist, but release certification requires live dashboard verification, production-signed builds, physical-device tests, production auth/payment verification, database restore proof, and monitoring evidence.

## Official Research Log

| Area | Source | Decision taken |
| --- | --- | --- |
| Expo app name/config | https://docs.expo.dev/versions/v56.0.0/config/app/ | Keep `expo.name` as `Simame`; update custom splash/onboarding text separately because JSX text is not controlled by native app config. |
| Expo env vars | https://docs.expo.dev/eas/environment-variables/ | Treat all `EXPO_PUBLIC_*` values as public; keep secrets out of mobile config and require strict EAS env checks. |
| Expo OTA/runtime | https://docs.expo.dev/eas-update/runtime-versions/ | Keep runtime/channel changes gated; native-affecting changes require a new binary, not OTA only. |
| Clerk Expo production | https://clerk.com/docs/guides/development/deployment/expo | Production requires Clerk Native Applications configuration and production instance values for bundle/package IDs and redirect allowlists. |
| Apple TestFlight | https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers | Plan internal TestFlight groups before public App Store submission. |
| Google Play testing | https://support.google.com/googleplay/android-developer/answer/14151465 | If the account is a new personal account, closed testing must satisfy the current tester/duration requirement before production access. |
| Google Data Safety | https://support.google.com/googleplay/android-developer/answer/10787469 | Do not guess privacy declarations; inventory real collected/shared data first. |
| Django deploy | https://docs.djangoproject.com/en/dev/howto/deployment/checklist/ | Require `check --deploy`, secret handling, HTTPS/security settings, and production server verification. |
| Supabase/Postgres | https://supabase.com/docs/guides/database/connecting-to-postgres | Use direct DB connections for migrations/backup/restore and pooler modes intentionally for app traffic. |
| Paystack authentication | https://paystack.com/docs/api/authentication/ | Enforce test/live key separation and keep secret keys backend-only. |
| Paystack transactions | https://paystack.com/docs/api/transaction/ | Initialize on the backend, use subunits and unique references, then verify by reference. |
| Paystack payments | https://paystack.com/docs/payments/accept-payments/ | Backend must initialize and verify payments; frontend callbacks are not truth. |
| Paystack webhooks | https://paystack.com/docs/payments/webhooks/ | Verify webhook signatures and make webhook handling idempotent. |
| Sentry for Expo | https://docs.expo.dev/guides/using-sentry/ | Require production release tags and source map upload evidence for mobile builds and updates. |

## Architecture Inventory

- Customer mobile: `connect-customer-mobile`, Expo SDK 54, Expo Router, Clerk Expo, Sentry React Native, EAS Build/Update, Expo Notifications, Paystack flow through backend APIs.
- Backend: `connect_new_backend`, Django 6, DRF, drf-spectacular, PostgreSQL via `DATABASE_URL`, Redis/Celery, Cloudinary storage, Paystack, Sentry, Render-style service definitions.
- Owner platform: `Connect-Web-App`, Next.js app with BFF proxy to the Django API and Sentry config files.
- Admin platform: Django admin/unfold plus backend analytics/admin endpoints.
- Workers: Celery worker and Celery beat are required for scheduled notifications/campaigns.

## Environment Matrix Finding

Current repository config does not prove three hard-separated environments. `eas.json` production uses the same Render API host seen across profiles and still references Clerk test credentials and `*.clerk.accounts.dev` URLs. That is a release blocker.

## Release Blockers

| Severity | Finding | Evidence | Required fix |
| --- | --- | --- | --- |
| P0 | Production mobile profile uses Clerk test credentials. | `connect-customer-mobile/eas.json` production contains `pk_test_...` and `grown-mole-74.clerk.accounts.dev`. | Configure production Clerk instance, native applications, redirect allowlists, and production publishable key. |
| P0 | Real production-signed mobile builds were not created or tested. | No `eas build --profile production` or App Store/Play dashboard proof was run in this turn. | Build iOS/Android production artifacts and test through TestFlight/Play tracks on physical devices. |
| P0 | Strict mobile release configuration fails. | validate:release:strict reports 18 missing or invalid production inputs, including API/EAS/Sentry/legal URLs, SSL pins, and OTA signing material. | Populate the production EAS environment and rerun the strict validator until it exits cleanly. |
| P1 | Mobile dependency audit retains high-severity advisories. | Compatible fixes reduced the audit to 25 findings, but 10 high image-size/Metro findings remain; npm only offers a breaking forced Expo downgrade. | Track the upstream Expo/Metro remediation or perform a planned supported Expo upgrade; do not force-downgrade the release branch. |
| P1 | Owner web release gates are unproven on this machine. | Lockfile audit is clean, but npm ci exhausted disk space before lint, tests, TypeScript, or Next production build could run. | Free disk space, run npm ci, then run lint, test, tsc, and build in CI or a clean workstation. |
| P0 | Paystack live payment/webhook flow is not certified. | Local controls now pass 38 focused payment/lifecycle tests and the complete 689-test backend suite, but no live dashboard, live charge, deployed signed webhook, production callback, refund, or reconciliation evidence exists. | Execute the live evidence matrix in PAYSTACK_PRODUCTION_AUDIT.md. |
| P0 | Backup restore is unproven. | No production backup restore test evidence exists here. | Restore latest backup to non-production and record RPO/RTO evidence. |
| P0 | Exposed credentials require rotation. | A Clerk webhook secret-shaped value exists in git history and an Expo access token-shaped value was present in the working tree; both are now redacted. | Rotate both provider credentials and verify old credentials are revoked before release. |
| P1 | Environment separation is unproven. | Mobile profiles share `connect-full-backend.onrender.com`; production may be sharing staging/test infrastructure. | Produce verified dev/staging/prod matrix with database, backend, Clerk, Paystack, storage, Redis, Sentry, email, push. |
| P1 | Readiness endpoint is not separately proven. | Backend has `/health/`; no dedicated `/readiness/` route was found in the quick scan. | Add or verify readiness that checks traffic-serving dependencies without breaking liveness. |
| P1 | Store privacy declarations are not evidence-backed. | Data Safety/App Privacy forms require exact data collection/sharing inventory. | Complete privacy inventory and map SDK data practices before submission. |
| P2 | Business email defaults include a personal email. | `config/settings.py` fallback `DEFAULT_FROM_EMAIL` and laundry approval notification fallback include a personal address. | Require configured Simame business sender/notification addresses in production. |
| P2 | Production domains still carry Connect branding. | Mobile env URLs include `connectlaundry.com` privacy/support domains. | Update to approved Simame domains before store submission. |

## Readiness Scores

- Backend readiness: 5/10
- Mobile readiness: 5/10
- Database readiness: 4/10
- Security: 4/10
- Payments: 6/10 for local controls; live certification remains blocked
- Reliability: 4/10
- Observability: 4/10
- iOS readiness: 3/10
- Android readiness: 3/10
- Overall: 4/10

## Manual Actions for Philip

| Service | Dashboard location | Exact field | Expected value | Environment | Why required | How to verify |
| --- | --- | --- | --- | --- | --- | --- |
| Apple Developer | Certificates, IDs & Profiles | Bundle ID | `com.connectlaundry.app` unless migrating IDs | Production | App signing, push, and App Store identity | Bundle ID matches EAS/iOS config and App Store Connect app record |
| App Store Connect | App Information | Name | Simame | Production | Customer-facing store identity | App Store Connect shows Simame before TestFlight/App Review |
| Expo/EAS | Project > Environment Variables | Production env | Real production values, no test Clerk, no mock API | Production | Correct binary config | `eas env:list --environment production` and `npm.cmd run validate:release:strict` |
| Clerk | Native Applications | iOS Bundle ID, Team ID, Android package, SHA-256, redirect allowlist | Match production bundle/package/signing | Production | Native OAuth/session security | Real device Google/Apple login works in release build |
| Google Cloud/OAuth | OAuth clients | Android/iOS app identifiers and SHA fingerprints | Production package/bundle and signing certs | Production | Google sign-in must trust store builds | Login succeeds only for the intended signed app |
| Paystack | Settings/API/Webhooks | Live keys and webhook URL | Backend-only live secret; public key as needed; HTTPS webhook | Production | Payment truth and reconciliation | Live test payment verifies via backend and signed webhook |
| Supabase/Postgres | Project Settings > Database | Direct/pooler URLs, SSL, backups | Workload-specific URLs and tested backups | Production | Data durability and connectivity | Restore drill passes in non-production |
| Backend host | Render/env dashboard | Django env vars | DEBUG false, SECRET_KEY, ALLOWED_HOSTS, CORS/CSRF, DB, Redis, email, Paystack, Sentry | Production | Secure serving | `check --deploy`, health/readiness, smoke tests pass |
| Cloudinary/storage | Cloudinary dashboard | Cloud name/API credentials | Production cloud and restricted credentials | Production | Media durability | Upload/delete tests pass without corrupting business records |
| Redis | Provider dashboard | Redis URL and persistence/limits | Production Redis | Production | Celery/cache reliability | Worker/beat tasks execute and broker outage behavior is known |
| SMTP/email | Email provider/DNS | Sender, SPF, DKIM, DMARC | Simame business sender | Production | Transactional trust and deliverability | Password reset/admin emails arrive and DNS checks pass |
| Sentry | Projects/releases | DSNs, env, releases, source maps | Mobile/backend/owner production projects | Production | Crash/error visibility | Safe synthetic staging error appears with release/env tags |
| Google Play | Play Console | Package, Play App Signing, Data Safety, testing tracks | Simame listing and production AAB | Production | Android distribution | Play-distributed build installs and passes smoke tests |
| Domain/DNS | DNS provider | API, web, privacy, support domains | Approved Simame domains | Production | Store metadata and OAuth redirects | HTTPS, CORS, CSRF, Clerk redirects, privacy/support URLs work |
| Privacy Policy | Website/legal docs | Policy and deletion URL | Accurate Simame privacy/account deletion pages | Production | Store compliance and user trust | Data Safety/App Privacy answers match policy and real behavior |

## Final Checklist Before Changing Verdict

- No unresolved P0/P1 findings.
- Production iOS TestFlight build works on physical iPhones.
- Production Android AAB works through Play testing on physical Android devices.
- Production database backup and restore have been tested.
- Paystack live verification/webhooks are proven.
- Clerk production auth is proven.
- Sentry production monitoring is active with release tags.
- Rollback and incident runbooks are accepted by the release owner.
## Paystack Deep Audit Update - 2026-08-13

The current local flow is backend-authoritative and now prevents the mobile app from opening a second Paystack transaction after order creation. The backend persists the original access code, serializes initialization on the order row, reuses every still-pending reference, uses Decimal-safe subunit conversion, requires exact reference, amount, currency, order, user, and domain verification, and creates the laundry settlement ledger from webhook, authenticated verify, or scheduled reconciliation.

An HTTPS callback bridge now redirects only to the fixed Simame app scheme. Production checks reject test Paystack keys, non-HTTPS provider callbacks, an invalid app callback scheme, and non-GHS currency.

Evidence: 38 focused backend payment/lifecycle tests passed; the canonical backend suite passed 689 tests; mobile typecheck and 255 tests passed; Expo Doctor passed 18/18 checks; OpenAPI generation completed with 0 errors and 3 enum-name warnings. See PAYSTACK_PRODUCTION_AUDIT.md.

This does not change the overall NO-GO verdict because live Paystack, production credentials, dashboard webhook delivery, production-signed device callbacks, backup restore, production auth, and monitoring are still unproven.

## Credential Rotation Blocker - 2026-08-13

A Clerk webhook secret-shaped value was found in .env.example history and an Expo access token-shaped value was found in the local working tree. The values were replaced with placeholders. Philip must rotate and revoke both credentials in Clerk and Expo before any TestFlight, Play, or public release. Do not record the replacement values in this report.
## Automated Verification - 2026-08-13

| Gate | Result |
| --- | --- |
| Backend full test suite | PASS - 689 passed, 14 warnings |
| Focused Paystack/lifecycle suite | PASS - 38 passed |
| Django system check | PASS - 0 issues |
| Migration drift / plan | PASS - no changes; no planned operations |
| OpenAPI generation | PASS WITH WARNINGS - 0 errors, 3 enum-name warnings |
| Mobile TypeScript | PASS |
| Mobile Jest | PASS - 30 suites, 255 tests |
| Expo Doctor | PASS - 18/18 |
| Mobile lint | PASS WITH WARNINGS - 0 errors, 49 warnings |
| Strict mobile release config | FAIL - 18 missing or invalid production inputs |
| Mobile production dependency audit | FAIL - 25 findings, including 10 high |
| Owner web lockfile audit | PASS - 0 vulnerabilities after compatible lock update |
| Owner web lint/test/typecheck/build | BLOCKED - npm ci exhausted disk space |
| Backend dependency consistency | WARN - app pin is correct; shared Python environment conflicts with globally installed Semgrep |
| Production signed device builds | NOT RUN |
| Live Paystack transaction/webhook/refund | NOT RUN |

The failed strict configuration gate and unexecuted live/device checks keep the release verdict at NO-GO.
