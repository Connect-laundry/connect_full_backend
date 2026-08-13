# Simame Manual Production Actions

Audit date: 2026-08-13

Only actions requiring account ownership, billing approval, production secrets, physical devices, store consoles or a real financial transaction are here. Do not paste secret values into tickets, reports or source control.

## P0 Actions Before Any Production Build

| Action | Why | Dashboard/service and exact setting | Value type | Verify success | Risk if skipped |
|---|---|---|---|---|---|
| Rotate the previously exposed Expo access token | A leaked token can authorize project actions | Expo/EAS account -> Access Tokens; revoke old token, create least-privilege replacement; Render `EXPO_ACCESS_TOKEN` and CI secret | SECRET | Old token fails; worker/beat can send a test push using replacement; no value in logs | Unauthorized builds/updates/push access |
| Rotate Clerk webhook signing secret | Existing value was exposed in prior evidence | Clerk production instance -> Webhooks -> endpoint signing secret; Render `CLERK_WEBHOOK_SIGNING_SECRET` | SECRET | Signed test webhook succeeds; old signature receives 401/400; user sync tests still pass | Forged identity synchronization |
| Create/configure Clerk production instance | EAS profiles currently contain a test publishable key and development Clerk domain | Clerk Dashboard -> production instance, Domains, Native applications, Social connections | account configuration | Production app signs up/in on Android+iOS; no `.clerk.accounts.dev` traffic | Broken or non-production authentication |
| Install production Clerk keys correctly | Mobile may only receive publishable key; backend receives secret | EAS `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY`; Render `CLERK_SECRET_KEY` and publishable/domain settings | PUBLIC mobile key; SECRET backend key | Strict validation passes; bundle contains no `sk_*`; backend token verification succeeds | Auth compromise or release login failure |
| Configure native OAuth identities | Redirect/package/signing identity must match stores | Clerk native app + Google Cloud/Apple Developer; Android package `com.connectlaundry.app`, production signing SHA-256; iOS bundle `com.connectlaundry.app`, Apple Team ID and service IDs | identifiers/cert fingerprints/SECRET client secrets | Google/Apple login on signed physical builds, cold and warm callback | OAuth rejected after store install |
| Populate EAS production environment | Strict validator currently fails | Expo project -> Environment Variables -> production; build profile already declares `environment: production` | see inventory below | `npm.cmd run validate:release:strict` passes in an environment that contains production values | Invalid or development-configured production binary |
| Configure production Paystack | No live transaction was run | Paystack Dashboard -> API Keys & Webhooks; Render `PAYSTACK_SECRET_KEY`, public key if required server-side, mode/domain; webhook URL `/api/v1/payments/paystack/webhook/` | SECRET keys and HTTPS URL | Paystack dashboard test delivery succeeds; backend config check passes; domain is live | Lost money, false payment state or no webhook settlement |
| Apply reviewed COD schema migrations during the backend release | The configured database has `ordering.0018_order_payment_method` and `payments.0012_payment_amount_collected_payment_collected_by_and_more` pending | Release migration job: `python manage.py migrate --noinput`; back up first and deploy backend/mobile/owner contract together | database schema change | `python manage.py showmigrations ordering payments` shows both as `[X]`; `python manage.py migrate --check` exits 0; COD smoke tests pass | Runtime field/query failures or incomplete legacy payment-method backfill |
| Resolve degraded live backend health | Current `/health/` returns HTTP 200 with `status=degraded` | Render backend service logs/metrics and dependency dashboards | operational diagnosis | `/health/` and deployed `/readiness/` return healthy; alerts fire on degradation | Release points at an unhealthy dependency |
| Apply reviewed Render Blueprint/plan changes | Local Blueprint now uses paid persistent production-capable plans and readiness path; no remote/billing action was taken | Render Blueprint/service settings: web/worker/beat `starter`, Redis `starter`, Postgres `basic-256mb`, health `/readiness/` | billing/plan approval | Blueprint validates, deploy succeeds, web/worker/beat run, readiness healthy | Free/ephemeral services, unavailable worker plan, no durable backups |
| Publish external account deletion page | Google Play requires an external deletion mechanism/link for apps with account creation | Public website at `EXPO_PUBLIC_DATA_DELETION_URL`; Play Console account deletion declaration | PUBLIC HTTPS URL/content | Logged-out user can submit/understand deletion; link returns 200 and matches in-app semantics | Store rejection and user-rights failure |

## Strict EAS Variable Inventory

The strict check reports 15 missing variable names plus three derived validation failures (real project UUID, at least two SSL pins, and pin expiry at least 45 days out).

| Variable | Class | Belongs in | Purpose |
|---|---|---|---|
| `EXPO_PUBLIC_API_URL` | PUBLIC | EAS production | Production API base URL |
| `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` | SENSITIVE | EAS production; restrict in Google Cloud by package/signing identity | Native maps |
| `EXPO_PUBLIC_MAPBOX_API_KEY` | SENSITIVE | EAS production; URL/package scope where supported | Map tiles/geocoding |
| `EXPO_PUBLIC_EAS_PROJECT_ID` | PUBLIC | EAS production/project config | Expo push/project identity; must be real UUID |
| `EXPO_PUBLIC_SENTRY_DSN` | PUBLIC/SENSITIVE | EAS production and Sentry project | Mobile crash transport; DSN is not an auth secret but should be scoped |
| `EXPO_PUBLIC_PRIVACY_POLICY_URL` | PUBLIC | EAS production/store dashboards | Hosted privacy policy |
| `EXPO_PUBLIC_TERMS_URL` | PUBLIC | EAS production/store dashboards | Hosted terms |
| `EXPO_PUBLIC_DATA_DELETION_URL` | PUBLIC | EAS production/Play Console | External account deletion |
| `EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL` | PUBLIC | EAS production/store dashboards | Privacy contact |
| `EXPO_PUBLIC_SUPPORT_EMAIL` | PUBLIC | EAS production/store dashboards | Customer support |
| `EXPO_PUBLIC_SSL_PINNING_HASHES` | PUBLIC | EAS production | At least two current backup-aware SPKI hashes |
| `EXPO_PUBLIC_SSL_PINNING_EXPIRATION_DATE` | PUBLIC | EAS production | Rotation fail-safe, at least 45 days after validation |
| `EXPO_UPDATES_URL` | PUBLIC | EAS production/project configuration | OTA update URL |
| `EXPO_UPDATES_CODE_SIGNING_CERTIFICATE` | FILE | EAS file secret / secure CI | Public certificate embedded for update verification |
| `EXPO_UPDATES_CODE_SIGNING_KEY_ID` | PUBLIC | EAS production | Identifies the signing key/certificate |

The private OTA signing key itself is a **SECRET FILE** kept outside the mobile bundle and used only by the authorized update-signing pipeline.

## Authentication Checklist

1. Create the Clerk production domain and production instance.
2. Set production publishable/secret keys in EAS and Render, never in tracked files.
3. Register webhook endpoint and rotated signing secret.
4. Register Android package and production SHA-256 signing fingerprint.
5. Register iOS bundle ID and Apple Team ID/entitlements.
6. Configure Google/Apple OAuth production credentials and redirect allowlist for `connect-laundry://` callbacks.
7. Verify logout, session revocation, account deletion and webhook reconciliation on signed physical builds.

Reference: [Clerk programmatic user deletion](https://clerk.com/docs/guides/users/managing).

## Payment Live Test Checklist

1. Confirm Render mode and keys are live and no `sk_test_` value is present.
2. Configure the exact HTTPS Paystack webhook and verify signature rejection for invalid requests.
3. Run one minimum authorized charge from a signed release candidate.
4. Compare amount/currency/reference/metadata in mobile, backend record and Paystack dashboard.
5. Replay callback/webhook to prove idempotency.
6. Run one owner-approved live refund and observe `REFUND_PENDING` then `REFUNDED` in mobile.
7. Reconcile ledger, fees, settlement and order state. Record redacted evidence.

## Backup And Restore Drill

Source runbook: `docs/production/DATABASE_RECOVERY.md`.

- **Method:** Render/Postgres managed backup or encrypted `pg_dump` from an approved production replica/snapshot.
- **Frequency/retention:** owner must choose and configure production plan/policy; daily with tested point-in-time recovery is recommended, not asserted as current.
- **Storage/encryption:** provider-managed encrypted storage or encrypted restricted bucket; confirm region/access logs.
- **Safe rehearsal:** restore the latest backup into a new isolated non-production database. Never overwrite production.
- **Validation:** run migrations check; compare table counts/checksums for users, orders, payments and migrations; sample relational integrity; run read-only smoke tests.
- **Reconnect:** point a temporary backend at restored DB, verify health and core read flows, then destroy/retain according to security policy.
- **RTO/RPO:** business owner must set targets and compare measured restore duration/backup age.
- **Rollback:** keep production untouched during rehearsal; for a real incident, freeze writes, preserve evidence, restore to a new service, validate, switch connection atomically and monitor.

Success evidence must include backup timestamp, restore start/end, backup age, validation results and named approver. A configuration file is not recovery proof.

## Store And Device Actions

| Action | Service | Verify | Risk if skipped |
|---|---|---|---|
| Inspect signed Android AAB | EAS/Play Console bundle explorer | package, versionCode, target API 36, merged permissions, production endpoints/Clerk key, signing certificate | API-level/store rejection or wrong config |
| Inspect signed iOS archive | EAS/App Store Connect/Xcode organizer | bundle/build, entitlements, privacy manifests, deep links, production config | App review/runtime failure |
| Complete privacy forms | Play Console Data Safety; App Store Connect App Privacy | approved answers match `STORE_DATA_DISCLOSURE_MATRIX.md` and SDK dashboards | Store rejection/misrepresentation |
| Run physical acceptance plan | Signed builds on actual devices | all sign-off rows completed with evidence | Undetected native/deep-link/push defects |
| Configure monitoring/alerts | Render/Sentry/Paystack/Clerk/Expo | test alert for 5xx, degraded health, payment/webhook failure, worker/beat failure | Silent production incidents |
| Recruit closed testers | Play closed testing/TestFlight | required tester cohort/time and feedback process satisfied | Cannot progress through store testing policy |
| Submit stores | Play Console/App Store Connect | only after release owner signs final certification | Premature public exposure |

## Explicitly Prohibited During This Audit

No credential rotation, billing/plan change, deployment, production EAS build, OTA publish, store submission, real charge/refund or destructive restore was performed.

## COD Reconciliation And Cash Refund Policy

COD cash is collected directly by the laundry and is displayed separately from platform-held Paystack balances. Operations should reconcile the order ID, expected amount, collected amount, collector and timestamp against the laundry's cash records. A COD collection must never be represented as a Paystack settlement or payout.

There is no approved automated cash-refund workflow. **MANUAL PRODUCT POLICY REQUIRED FOR POST-COLLECTION CASH REFUNDS.** Until product/legal/accounting approve a workflow, do not call Paystack for cash, do not create a provider refund reference, and document any owner-managed cash return outside Simame's Paystack ledger.
