# SIMAME NOTIFICATION PLATFORM PRODUCTION CERTIFICATION

Certification date: 2026-08-27

Scope: Simame customer mobile app, Django notification backend, Celery worker and beat scheduling, Expo Push Service, APNs and FCM handoff, authentication startup, booking navigation, and notification operational controls.

Evidence rule: static configuration and automated tests are reported separately from provider and physical-device proof. No physical delivery claim is made.

## ARCHITECTURE

Final transactional path:

`business event -> NotificationService -> atomic NotificationEventClaim plus Notification row -> transaction.on_commit queue request -> notifications Celery queue -> Expo batches of at most 100 -> persisted PushDelivery ticket -> delayed Expo receipt lookup in batches of at most 1,000 -> APNs or FCM -> OS notification -> authenticated deep link`

The in-app record is durable before push delivery is attempted. Push never runs inline in an order, payment, authentication, account, or campaign request. If Redis or Celery is unavailable, the row remains `PENDING`; the minute-level `dispatch_pending_pushes` sweep recovers it. Campaign work uses the lower-priority `campaigns` queue, while transactional delivery and recovery use the `notifications` queue.

Relevant provider rules were checked against the official [Expo send and receipt guidance](https://docs.expo.dev/push-notifications/sending-notifications/), [Expo FCM V1 setup](https://docs.expo.dev/push-notifications/fcm-credentials/), [Expo setup guide](https://docs.expo.dev/push-notifications/push-notifications-setup/), [Apple notification permission guidance](https://developer.apple.com/documentation/UserNotifications/asking-permission-to-use-notifications), [Apple notification handling guidance](https://developer.apple.com/documentation/usernotifications/handling-notifications-and-notification-related-actions), [Android notification permission guidance](https://developer.android.com/develop/ui/compose/notifications/notification-permission), and [Firebase Android receiving guidance](https://firebase.google.com/docs/cloud-messaging/android/receive-messages).

## STARTUP SAFETY

```text
Push initialization blocks app startup: NO
Push failure can block login: NO
Push failure can break orders: NO
```

Registration starts only after authenticated app initialization and is fire-and-observe. Expo token issuance and backend synchronization each have a 15-second bound, backoff, and non-fatal error handling. Authentication bootstrap no longer classifies every 4xx response as an expired session, duplicate expiry alerts are suppressed, and navigation waits for a single resolved auth state. Booking navigation now validates required data and has bounded loading/error exits instead of indefinite spinners.

## DEVICE LIFECYCLE

```text
Registration: authenticated physical builds request OS permission, obtain an Expo token, and sync token/install/platform/version/environment
Token refresh: Expo token listener syncs the replacement before storing it locally
Multi-device: supported; each install has an independent device_id and active state
Logout: only the matching token/install is deactivated; local token and badge are cleared even if the API is unavailable
Account switch: globally unique token is atomically reassigned to the authenticated user and current server environment
Deletion: account anonymization deletes all related push devices and revokes sessions
Invalid token cleanup: DeviceNotRegistered deactivates the exact stored device from ticket or receipt evidence
```

The server owns the environment boundary. A production app claim sent to a staging backend, or the reverse, is rejected. All delivery queries are scoped by `PUSH_ENVIRONMENT`.

## DELIVERY PIPELINE

```text
Notification DB: durable in-app row plus PENDING/SENT/DELIVERED/SKIPPED/FAILED state
Celery: dedicated priority queues, bounded retries, minute-level pending recovery, beat required
Expo: access-token support, 10-second network timeout, at most 100 messages per request
Tickets: persisted per notification/device with error code and payload
Receipts: delayed lookup, at most 1,000 IDs per request, persisted success/error outcome
APNs: Expo handoff requested at high priority with active interruption level
FCM: Expo handoff uses versioned Android channel IDs; matching FCM V1 service-account key assigned in production EAS credentials
```

An Expo ticket means accepted for processing, not device delivery. `DELIVERED` is assigned only after an `ok` receipt.

## TRANSACTIONAL EVENTS

Actual customer/owner event coverage:

- Order placed for customer and new order received for laundry owner.
- Order `CONFIRMED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`, `CANCELLED`, and `REJECTED`.
- Quote ready.
- Cash collected.
- Payment success, payment failure, and payment received by laundry owner.
- Refund settled.

Payment-success producers share one event key so webhook, callback, reconciliation task, admin action, and cash path cannot create duplicate success notifications. Cancellation and rejection pushes no longer expose free-form reasons on the lock screen; details remain inside the authenticated app.

## PREFERENCES

```text
Categories: master push, order updates, payment updates, promotions, campaigns, referrals, weekly tips
Quiet hours: supported, including windows that cross midnight
Marketing opt-out: campaigns and promotions require the relevant opt-in
Critical events: URGENT bypasses quiet hours but still respects master/category opt-out
```

Preference blocking affects push only. The in-app notification history is still written.

## RETRY / FAILURE

```text
Expo timeout: requests fail with bounded timeout and Celery exponential retry
429: ticket MessageRateExceeded raises for Celery backoff; receipt rate limits return the notification to PENDING up to the configured cap
5xx: HTTP error raises for Celery exponential retry
InvalidCredentials: delivery fails visibly and a daily deduplicated admin system alert is created
DeviceNotRegistered: matching token is deactivated
Redis down: request/business transaction succeeds; PENDING row is retained
Celery down: PENDING recovery sweep sends after worker recovery
```

Campaign APIs return 503 and leave campaigns `SCHEDULED` when the broker cannot accept work. Expo/network work is never executed synchronously in the request process.

## DUPLICATE SAFETY

```text
Duplicate business event: SHA-256 NotificationEventClaim unique key gives database-enforced concurrency-safe deduplication
Duplicate Celery execution: terminal notification/delivery states make sends and receipt updates idempotent
Duplicate campaign: campaign-user dedup keys and state transitions prevent repeated recipient records
Duplicate device token: global token uniqueness plus atomic reassignment; prior token for the same install is retired
```

The old duplicate order-created receiver was removed. Read/unread state no longer changes deduplication behavior.

## PERFORMANCE

Measured deterministic transport construction with mocked Expo HTTP, 20 runs per size:

```text
100 push: 1 request; median 0.107 ms; p95 0.145 ms
500 push: 5 requests; median 0.525 ms; p95 0.579 ms
1,000 push: 10 requests; median 1.056 ms; p95 1.404 ms
queue lag: NOT MEASURED against deployed Redis/Celery
drain time: NOT MEASURED against live Expo/APNs/FCM
```

These timings prove payload construction and batch boundaries only. They do not predict production network latency or worker throughput. A live 100/500/1,000-recipient staging load rehearsal remains required.

## DATABASE SCALE

Notification indexes cover user/read/created, audience/read/created, category, push status, queue timestamp, and dedup key. Device indexes cover user/environment/active and install/environment. Delivery indexes cover notification/status and status/created. Feed pagination defaults to 50 and caps client requests at 100.

Growth is approximately one Notification row per recipient/event plus one PushDelivery row per active destination device. Exact monthly storage cannot be projected without event rate, active device count, and real row-size measurements.

Retention values exist only as policy placeholders. Automated deletion is intentionally disabled until the business/privacy owner approves legal retention, backup, audit, and recovery behavior. This is a remaining P1 scale/governance item.

## OBSERVABILITY

```text
metrics: pending count, oldest queue age, active/inactive devices, ticket errors, receipt outcomes/rate, retries, sends, failures, opens, clicks, conversions
Sentry: Django and Celery integrations configured when a valid DSN is supplied; mobile Sentry is environment-driven
alerts: InvalidCredentials and sender mismatch create deduplicated admin alerts; logs contain structured provider failures
failed-task visibility: Celery retries/logs plus durable PENDING/FAILED rows and PushDelivery details
```

External Sentry alert rules, queue dashboards, paging destinations, and measured notification SLOs were not verified. Queue lag and credential-error alerts should be wired to an on-call destination before launch.

## IOS

```text
APNs configured: STATIC YES; EAS APNs key observed previously; current end-to-end validity NOT TESTED
Foreground configuration: YES in code; physical presentation NOT TESTED
Background configuration: YES in code; physical presentation NOT TESTED
Terminated configuration: cold-start response handling exists; NOT TESTED
Sound: requested; NOT TESTED
Banner: requested; NOT TESTED
Lock Screen: requested and controlled by iOS user settings; NOT TESTED
Badge: server unread count and client reconciliation exist; NOT TESTED
Deep link: authenticated allow-listed routing exists; NOT TESTED
```

Static evidence includes `expo-notifications`, production `aps-environment`, handler presentation options, response listeners, and cold-start response recovery. Apple permission, Focus, Notification Summary, preview, sound, and badge behavior remain device/user-setting dependent.

## ANDROID

```text
FCM V1 configured: YES; client google-services.json and production EAS service-account project match
Permission: Android 13+ runtime request implemented; NOT TESTED
Channel importance: default HIGH, orders MAX, promotions DEFAULT; versioned as *_v2
Vibration: default/orders enabled; promotions intentionally disabled
Sound: default sound requested
Heads-up: high/max importance configured; NOT TESTED
Lock Screen: PRIVATE visibility configured; NOT TESTED
Badge: channel showBadge plus unread count implemented; launcher support varies; NOT TESTED
Deep link: authenticated allow-listed routing exists; NOT TESTED
```

The red app-icon number is only a badge. The requested banner, lock-screen card, sound, and vibration require a correctly credentialed fresh native build plus OS notification permission and channel settings.

## FIREBASE

```text
FIREBASE / FCM MANUAL ACTION REQUIRED:
NO
```

The `google-services.json` client configuration for `com.connectlaundry.app` and the private Firebase service-account project were validated as matching without printing credential contents. The service-account key was uploaded and assigned to the production Android FCM V1 slot in `@kusantec-solutions/connect-laundry` on 2026-08-27. EAS then reported the key as assigned. The local private file is excluded by an explicit Git ignore rule and is not tracked.

A fresh Android APK/AAB plus physical push delivery testing is still required. Credential assignment proves the provider path is configured; it does not prove device delivery.

## SECURITY

```text
Cross-user leak: no known path; feeds/device mutation require authentication and querysets are user-scoped
Token logged: complete token is not logged; diagnostics mask it
Sensitive push content: minimized; no addresses/payment values/free-form cancellation reasons; Android lock-screen visibility PRIVATE
Unauthenticated push endpoints: NO; registration, removal, feed, read/open/click endpoints require authentication
```

Deep-link paths are allow-listed and held behind the auth gate. Staging/production token separation is server enforced. Physical account-switch/reinstall testing remains required.

## FILES CHANGED

Backend notification and deployment files:

- `.env.example` — documents push environment, receipt, and recovery controls.
- `config/settings.py`, `config/test_settings.py` — environment validation, queues, priorities, retry/recovery settings, and beat schedule.
- `docker-compose.yml`, `render.yaml` — notifications/campaign queue consumption and production environment mapping.
- `marketplace/management/commands/validate_environment.py` — production push-environment validation.
- `marketplace/models/__init__.py`, `marketplace/models/notification.py` — event claims, delivery receipts, environment-aware devices, retry and queue timestamps.
- `marketplace/migrations/0016_alter_notification_push_status_pushdelivery.py` — delivery state and ticket persistence.
- `marketplace/migrations/0017_remove_pushdevice_marketplace_user_id_b69260_idx_and_more.py` — environment, retry, queue timestamp, and indexes.
- `marketplace/migrations/0018_notificationeventclaim.py` — concurrency-safe durable business-event claims.
- `marketplace/serializers.py`, `marketplace/views/notifications.py` — token validation, environment-safe lifecycle, and bounded pagination.
- `marketplace/services/notification_service.py`, `marketplace/services/campaign_service.py` — central durable, non-blocking dispatch and deduplication.
- `marketplace/tasks.py` — batching, tickets, receipts, retries, invalid-device cleanup, alerts, and pending recovery.
- `marketplace/signals.py`, `ordering/signals.py`, `ordering/views/lifecycle.py` — canonical event producers, privacy-safe copy, and duplicate removal.
- `payments/admin.py`, `payments/tasks.py`, `payments/views.py`, `payments/webhooks.py` — unified payment-success idempotency keys.
- `marketplace/views/campaigns.py` — queue/provider analytics.
- `marketplace/tests/test_notifications.py`, `marketplace/tests/test_campaign_center.py` — lifecycle, outage, dedup, environment, batching, receipt, and pagination coverage.
- `docs/production/SIMAME_NOTIFICATION_PLATFORM_PRODUCTION_CERTIFICATION.md` — this evidence report.

Mobile notification, authentication, booking, and release files:

- `.env.example`, `app.config.ts`, `eas.json`, `scripts/validate-release-config.mjs` — EAS project, app environment, Firebase client files, plugins, entitlements, and strict validation.
- `google-services.json`, `GoogleService-Info.plist` — user-supplied native Firebase client configuration; neither is an FCM server private key.
- `app/_layout.tsx`, `app/(tabs)/_layout.tsx`, `app/authScreens/signIn.tsx`, `app/authScreens/signUp.tsx` — deterministic auth navigation and non-blocking startup.
- `src/context/UserContext.tsx`, `src/services/auth.service.ts`, `src/services/http.ts`, `src/utils/authNavigation.ts` — session bootstrap, refresh serialization, expiry suppression, and navigation decisions.
- `src/services/pushNotification.service.ts`, `src/features/notifications/hooks/useNotificationObserver.ts`, `src/context/NotificationContext.tsx` — permission, channels, token lifecycle, badge, foreground/background/cold-start routing, and auth gate.
- `src/features/settings/hooks/useNotificationPreferences.ts` — preference synchronization.
- `src/features/booking/BookingScreen.tsx`, `src/features/booking/utils/checkoutNavigation.ts`, `src/features/checkout/CheckoutReviewScreen.tsx`, `src/features/laundry-details/LaundryDetailsScreen.tsx`, `src/features/laundry-details/components/BookButton.tsx`, `src/features/orders/screens/OrderPaymentScreen.tsx` — guarded booking/checkout navigation and error exits.
- `src/features/home/HomeTabScreen.tsx`, `src/features/home/hooks/useHome.ts`, `src/features/home/hooks/useHomeLocation.ts`, `src/hooks/useBoundedLoading.ts`, `src/utils/expo-ui.ts` — bounded loading and defensive native-module behavior.
- `docs/NOTIFICATION_SYSTEM.md`, `docs/NOTIFICATION_E2E_AND_HARDENING.md` — architecture and operational runbook.
- `__tests__/authNavigation.test.ts`, `__tests__/bookingNavigation.test.ts`, `__tests__/boundedLoading.test.tsx`, `__tests__/pushNotification.service.test.ts`, `__tests__/http.refreshQueue.test.ts`, `__tests__/notificationRoute.test.ts` — regression coverage.

## TEST RESULTS

```text
python -m pytest -q
750 passed, 1 skipped, 14 warnings, 4 subtests passed in 76.62s

python -m pytest marketplace/tests/test_notifications.py marketplace/tests/test_campaign_center.py -q
113 passed, 4 subtests passed in 6.87s

python manage.py check
0 issues

python manage.py makemigrations --check --dry-run
No changes detected

npx.cmd jest --runInBand --watchAll=false
35 suites passed; 293 tests passed; exit 0

npm.cmd run typecheck
Passed; exit 0

npm.cmd run lint
0 errors; 41 existing warnings

npm.cmd run validate:release
Release configuration validation passed

npx.cmd expo-doctor
18/18 checks passed

git diff --check
Passed; line-ending conversion notices only
```

Additional read-only environment evidence:

- `validate_environment --no-network`: 11 checks, 0 failures, 2 warnings.
- Production EAS config resolved production environment, `com.connectlaundry.app`, `./google-services.json`, `default_v2`, and production APNs entitlement.
- `check --deploy` remains failed by broader release configuration: Paystack test keys, non-HTTPS callback, and missing production Clerk publishable/webhook settings. Schema enum warnings also remain.
- Marketplace migrations 0016, 0017, and 0018 were applied successfully to the configured Supabase PostgreSQL database on 2026-08-27. Migration history, both new tables, and `manage.py check` were verified afterward.

## NATIVE BUILD REQUIRED

```text
iOS: YES — NEW iOS AD HOC/production build required
Android: YES — NEW Android APK/AAB required now that FCM V1 is assigned
```

The channel, entitlement, Firebase-client, and native plugin changes cannot be proven with the old installed binaries and are not an OTA-only release.

Physical matrix still required on fresh builds:

- iOS: foreground, background, terminated, lock screen, sound, banner, badge, tap/deep-link, permission deny/allow, logout/account switch, reinstall, and multiple devices.
- Android: foreground, background, terminated, lock screen, sound, vibration, heads-up, badge, tap/deep-link, permission deny/allow, channel settings, logout/account switch, reinstall, and multiple devices.

## REMAINING P0 / P1

P0 before physical push QA:

1. Deploy web, `notifications,celery,campaigns` worker, beat, and Redis with matching `PUSH_ENVIRONMENT` values.
2. Build fresh iOS and Android binaries and execute the complete physical matrix.

Broader P0 before a production release:

1. Replace Paystack test keys, use an HTTPS callback, and configure production Clerk publishable and webhook secrets; current `check --deploy` fails these controls.

P1:

1. Approve and implement notification/delivery retention plus a tested recovery policy.
2. Configure external Sentry/queue/credential alert rules and measure a notification SLO.
3. Run deployed staging load tests at 100, 500, and 1,000 recipients; capture queue lag, drain time, receipt latency, failure rate, and provider throttling.
4. Review and remove two diagnostic accounts created during an earlier configured-database reproduction (`dbg1@example.com`, `dbg2@example.com`) only with explicit data-deletion approval. No push devices were created because the unapplied schema stopped that operation.

## SCORE

```text
Notification Architecture 88/100
Delivery Reliability 82/100
Scale 76/100
Security 86/100
Observability 72/100
Physical Verification 0/100
```

The scores deliberately exclude unperformed hardware, live-provider, live-queue, load, retention, and alert-delivery proof.

## FINAL VERDICT

🟡 NOTIFICATION ARCHITECTURE SOUND — CONFIG/PROVIDER ACTIONS REMAIN
