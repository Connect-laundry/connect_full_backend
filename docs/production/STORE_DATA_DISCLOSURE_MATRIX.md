# Simame Store Data Disclosure Matrix

Audit date: 2026-08-13

This is an engineering data-flow inventory, not legal advice. Google Play Data Safety and Apple App Privacy answers must be approved by the business/privacy owner against actual production contracts, retention schedules, SDK dashboard settings, and published policies. Items marked **MANUAL** cannot be concluded from source alone.

| Data type | Collected | Required / optional | Product purpose | Stored server-side | Processor / SDK | Transit | Deletable | Retention / manual confirmation | Android permission | iOS permission | Store implication |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Name | YES | Required for account/order | identity, fulfillment, receipts | YES | Django/Postgres; Clerk | TLS | Profile data anonymized on deletion; transaction snapshots may remain | MANUAL legal/finance retention | none | none | Personal info; app functionality |
| Email | YES | Required for account/recovery | login, recovery, receipts/support | YES | Django, Clerk, email provider | TLS | Anonymized locally and Clerk identity deleted | MANUAL email logs and finance retention | none | none | Contact info; app functionality/account management |
| Phone | YES | Required for fulfillment | contact, order coordination | YES | Django; Clerk metadata if configured; notification/SMS provider if enabled | TLS | Cleared from active profile on deletion | MANUAL SMS provider retention | none | none | Contact info; app functionality |
| User/account ID | YES | Required | authorization, ownership, fraud/audit | YES | Django, Clerk, Sentry/analytics if user context enabled | TLS | Active identity removed; tombstoned DB key retained for referential integrity | MANUAL lawful retention and Sentry scrubbing | none | none | User IDs; app functionality/security |
| Authentication data | YES | Required | account access/session security | YES | Clerk and Django token/session tables | TLS | Sessions revoked; Clerk identity deleted | Tokens must never be logged | none | none | Sensitive info/security |
| Physical address | YES | Required for pickup/delivery | order fulfillment and service-area validation | YES | Django/Postgres; maps/geocoding processor | TLS | Address rows deleted on account deletion; historical order delivery records may remain | MANUAL transaction retention | none | none | Address; app functionality |
| Approximate location | YES | Optional until nearby/map use | local discovery/service area | Query/transient; may enter analytics/logs | OS location, maps provider, backend | TLS | User can deny; server/log retention MANUAL | Confirm analytics precision reduction | `ACCESS_COARSE_LOCATION` | `NSLocationWhenInUseUsageDescription` | Location; app functionality |
| Precise location | YES | Optional and feature-triggered | map positioning, distance, pickup context | Coordinates may be stored with selected address/order | Expo Location, maps provider, Django | TLS | Address deletion; retained order coordinates MANUAL | No background location found | `ACCESS_FINE_LOCATION` | `NSLocationWhenInUseUsageDescription` | Precise location disclosure required if production flow sends it |
| Payment/transaction history | YES | Required when paying | authorization, settlement, receipt, refund/audit | YES | Django/Postgres and Paystack | TLS | Intentionally retained/anonymized, not erased with account | MANUAL finance/tax/chargeback schedule | none | none | Purchases/financial info; app functionality/fraud prevention |
| Full card details | NO in Simame systems | Paystack-hosted payment only | payment authorization | NO in app/backend | Paystack-hosted checkout | TLS | Paystack policy | Verify no custom card form is introduced | none | none | Processor collects payment information; disclose per store guidance/contract |
| Profile photo / image | YES | Optional | avatar/profile | YES | media storage provider/backend | TLS | Current avatar reference and stored object deletion attempted | MANUAL storage backups/CDN retention | photo library/storage; camera only if reachable picker option | photo library/camera usage descriptions | Photos/videos; app functionality |
| Camera | POSSIBLE | Optional | capture avatar/upload image | Image result may be uploaded | Expo Image Picker and storage provider | TLS after user action | Same as profile photo | Confirm capture option remains reachable | `CAMERA` may be injected at native build if camera capture enabled | `NSCameraUsageDescription` | Permission and photos disclosure; test denial fallback |
| Microphone/audio | NO | Not used | none | NO | none | N/A | N/A | `expo-av` removed and image picker microphone permission disabled | removed | removed | No microphone disclosure expected after artifact verification |
| Device/push token | YES | Optional; required for push | notification delivery/device mapping | YES | Django and Expo Push Service/APNs/FCM | TLS | Device tokens deleted on account deletion and removable via endpoint | MANUAL provider receipt/log retention | `POST_NOTIFICATIONS` on Android 13+ is native/runtime generated | notification authorization | Device ID/other data; app functionality |
| Device integrity/security signals | YES when enforcement enabled | Required for protected release mode | anti-abuse, compromised-device controls, SSL pinning | May be logged as security events | OS integrity APIs, backend/Sentry | TLS | MANUAL | Confirm exact production integrity provider/settings | none or provider-generated | App Attest entitlement/config | Device/diagnostics disclosure may apply |
| Product analytics events | YES | Optional/operational | feature usage, reliability | YES | Simame analytics backend; possible Sentry breadcrumbs | TLS | Account deletion behavior depends on identifiers | MANUAL retention, consent and de-identification | none | none | Usage data/product interaction |
| Diagnostics/crash logs | YES when DSN configured | Operational | crash and API-failure diagnosis | Third-party | Sentry | TLS | MANUAL in Sentry dashboard | Verify PII scrubbing, retention, release/source-map setup | none | none | Diagnostics/crash data |
| Search/filter history | TRANSIENT | Optional | discovery | No dedicated history model found; may be analytics/logged | mobile memory/backend query logs/analytics | TLS | Session/cache clear; logs MANUAL | Confirm analytics event payloads | location only if nearby | location only if nearby | Search history/usage may apply if logged |
| Reviews and feedback | YES | Optional | marketplace trust/support | YES | Django/Postgres; support/email tooling | TLS | Profile anonymization does not necessarily erase published review/feedback | MANUAL moderation and retention policy | none | none | User content; app functionality |
| Order status/notification content | YES | Required for fulfillment | tracking and communications | YES | Django, Expo Push/APNs/FCM | TLS | Transaction record retained; notifications deleted/anonymized per policy | Ensure lock-screen payloads minimize sensitive details | notification permission | notification permission | User content/app activity may apply |

## Third-Party Data Flows To Confirm Manually

- **Clerk:** identity, email/phone, OAuth provider data, sessions and device metadata. Confirm production instance, region, retention and deletion behavior.
- **Paystack:** customer email, transaction reference, amount/currency and payment instrument data entered on Paystack's hosted page. Simame must not receive or log raw card data.
- **Expo Push / APNs / FCM:** push token, app/device delivery metadata and notification payload.
- **Sentry:** crash traces, release/build, device/app metadata and any user context/breadcrumbs enabled by configuration. Confirm PII scrubbing and source-map access.
- **Maps/geocoding provider (Google Maps and/or Mapbox):** coordinates, map requests, search/geocode inputs and device network metadata.
- **Media/storage provider:** uploaded avatar/photo and metadata; verify deletion propagation, backups and CDN caches.
- **Email/SMS provider:** contact details and message delivery metadata if production sending is enabled.
- **Render/Postgres/Redis:** application records, logs, queues and caches. Confirm region, backup, retention and subprocessors.

## Permission Evidence

Resolved Expo introspection after remediation:

- Android: coarse location, fine location, internet, read external storage, write external storage.
- iOS: location/camera/photo usage descriptions; microphone usage was removed.
- No background location, contacts, phone or SMS permission was found.

The final signed AAB/IPA remains authoritative. Android storage permissions may be removed/ignored by modern platform behavior but must still be checked in the merged release manifest.

## Manual Store Actions

1. Reconcile this matrix with the actual production SDK dashboards and contracts.
2. Publish a Simame privacy policy and external account-deletion page at the URLs configured in EAS.
3. Complete Google Data Safety and Apple App Privacy forms from the approved inventory.
4. Verify data-retention periods for financial, fraud, support, analytics, logs, backups and media.
5. Inspect a signed release artifact and update the permission rows if manifest merging differs.