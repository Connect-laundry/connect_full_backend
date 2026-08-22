# SIMAME PRODUCTION READINESS & RESILIENCE REPORT

**Certification date:** 2026-08-20  
**Decision:** **🔴 NO-GO**

## Evidence Legend

- **PASS - tested:** the check actually ran.
- **PASS - statically verified:** code or configuration was directly inspected.
- **FAILED:** an executed check failed.
- **BLOCKED:** safe execution required unavailable isolation, access, credentials, hardware, or tooling.
- **NOT TESTED:** no claim is made.

## Executive Decision

Simame has a strong functional code base and materially safer payment paths after this audit. The backend passed **732 tests**, mobile passed **269 tests**, the owner portal passed **8 tests**, and Expo Doctor passed **18/18 checks**.

It is not certified for real customers, laundries, or live money:

1. Both documented live readiness endpoints returned HTTP 200 with {"status":"degraded"} on 2026-08-20.
2. Absolute staging/production separation is not proven. The repository declares one Render database, Key Value service, web service, worker, and beat service.
3. The strict mobile release gate fails on six items: isolated Sentry, three Simame legal URLs, and two OTA signing values.
4. No authorized production-like staging load, stress, soak, signup burst, or fault-injection run was completed.
5. Database restore, deployment rollback, alert delivery, and controlled Sentry receipt were not demonstrated.
6. The complete physical iOS and Android journey was not tested.
7. Paystack live-mode reconciliation and dashboard webhook configuration were not demonstrated.
8. The owner production build did not complete on this host because the native build process exhausted memory.

No unresolved code-level P0 is known after the payment fixes. Multiple P1 release blockers remain.

## Audited Targets

| Surface | Branch | Base commit |
|---|---|---|
| Django API | staging | 965e4329640e938e424f8762f236059d1d71c0f2 |
| Customer mobile | main | 957b87ffb2bcc63dd5df333afedf65f5643dcf13 |
| Owner portal | release/simame-rc1 | 19acb7f594e3bc7fb7ad7c078c1f3e4f9f61507c |

These are different branches/commits. A single immutable cross-repository release candidate is not identified.

## Real Dependency Graph

    Customer Expo app              Owner Next.js app / Django admin
          | Clerk token                    | session/token
          +---------------- HTTPS ----------+
                              |
                        Django + DRF
         synchronous: auth, catalogue, orders, quotes, coupons,
         payment initialization/verification, owner transitions,
         account and media APIs
              |                 |                 |
          PostgreSQL         Paystack         Cloudinary
          authority          money flow       media
              |
          transaction.on_commit
              |
       Redis/Valkey broker + cache + DRF throttles
              |
        Celery worker <---- Celery beat
              |
       Expo Push, SMTP, campaigns, weather, OCR/analytics

    Cross-cutting: Sentry, redacted structured logs, Render health checks

**Synchronous failure domains:** Django, PostgreSQL, Clerk verification, Paystack initialization/verification, and required media upload.  
**Asynchronous failure domains:** push, email, campaigns, scheduled jobs, and other Celery work.  
**Source of truth:** PostgreSQL. Redis is not authoritative for orders or payments.  
**Declared SPOFs:** one small DB, broker/cache, web class, worker, beat, region, DNS, Clerk, Paystack, and Cloudinary.

## Current Official Provider Facts

- Expo lists Free, Starter at $19/month, Production at $199/month, and Enterprise. Production includes 50K Update MAU; Enterprise advertises an SLA: https://expo.dev/pricing
- Expo Push limits sends to 600 notifications/second/project, 100 messages/request, and 1,000 receipt IDs/request. Tickets are not delivery proof; receipts must be checked: https://docs.expo.dev/push-notifications/sending-notifications/
- Expo Update code signing keeps the private key outside source and embeds the public certificate in the binary: https://docs.expo.dev/eas-update/code-signing/
- Clerk documents frontend signup/sign-in limits of 5 requests/10 seconds/IP, factor attempts of 3/10 seconds/IP, and production backend limits of 1,000 requests/10 seconds/instance: https://clerk.com/docs/guides/how-clerk-works/system-limits
- Clerk webhooks are asynchronous and use Svix retries/replay: https://clerk.com/docs/guides/development/webhooks/overview
- Clerk Hobby includes up to 50K monthly retained users: https://clerk.com/pricing
- Paystack requires server initialization, unique references, subunit amounts, signed webhooks, and server verification before fulfillment: https://paystack.com/docs/payments/accept-payments/ and https://paystack.com/docs/payments/verify-payments/
- Paystack live webhook retries continue for up to 72 hours when the endpoint does not return 200: https://paystack.com/docs/payments/webhooks/
- Paystack Ghana publishes a 1.95% transaction fee and T+1 settlement: https://paystack.com/gh/pricing
- Paid Render Postgres provides PITR and restore creates an isolated DB; free services are unsuitable for production: https://render.com/docs/postgresql-backups and https://render.com/docs/free
- Paid Render Key Value enables disk persistence; new instances use Valkey 8: https://render.com/docs/key-value
- Django requires a production settings review and check --deploy: https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/
- DRF throttling is cache-based, race-prone, and not a strict security control: https://www.django-rest-framework.org/api-guide/throttling/
- Celery late acknowledgement permits duplicate execution after worker loss; tasks must be idempotent: https://docs.celeryq.dev/en/stable/userguide/tasks.html
- Cloudinary Free provides 25 monthly credits; one credit can represent 1 GB storage, 1 GB bandwidth, or 1,000 transformations: https://cloudinary.com/documentation/billing_and_plans
- Sentry lists Developer $0, Team $26/month, and Business $80/month: https://sentry.io/pricing/
- Apple requires an accessible privacy policy, accurate first/third-party data disclosure, and in-app account deletion: https://developer.apple.com/app-store/review/guidelines/ and https://developer.apple.com/support/offering-account-deletion-in-your-app
- Google Play requires an in-app deletion path and a public deletion web resource: https://support.google.com/googleplay/android-developer/answer/13327111

Actual subscribed plans and dashboard quotas were not accessible. Repository plan names are not billing proof.

## Environment Isolation

| Control | Staging | Production | Result |
|---|---|---|---|
| Backend URL | connect-full-backend.onrender.com | connect-full-backend-production.onrender.com | PASS - statically verified |
| Clerk key family | test | live | PASS - statically verified |
| Paystack mode | Not visible | Not visible | BLOCKED |
| PostgreSQL | Not identifiable | Not identifiable | BLOCKED |
| Redis/Valkey | Not identifiable | Not identifiable | BLOCKED |
| Cloudinary | Not identifiable | Not identifiable | BLOCKED |
| Sentry | Same mobile project | Same mobile project | FAILED |
| Render topology | One blueprint stack | No second declared stack | FAILED as isolation evidence |
| Readiness | HTTP 200 degraded | HTTP 200 degraded | FAILED |

A URL difference is not infrastructure isolation. Record DB host/name, Key Value ID, Cloudinary cloud, Paystack mode, Clerk instance, and Sentry project for both environments before chaos testing.

## Production SLOs, RPO, and RTO

| Signal | Launch target |
|---|---|
| API availability | >= 99.9% monthly |
| Authenticated request success | >= 99.5%, excluding invalid credentials |
| Order creation | >= 99.9%; zero duplicate orders per idempotency key |
| Payment reconciliation | 100% eventual; 99.9% within 5 minutes |
| API latency | p50 < 250 ms, p95 < 750 ms, p99 < 1.5 s |
| API 5xx rate | < 1% over 5 minutes; < 0.2% daily |
| Transactional task start | p95 < 60 seconds |
| Push provider acceptance | >= 99%; receipt errors separately measured |
| DB connections | warning 70%, critical 85% |
| Queue age | warning 2 minutes, critical 10 minutes |
| RTO | 60 minutes for core order/payment API |
| RPO | 5 minutes for PostgreSQL; zero loss for confirmed payment evidence |

These are proposed targets, not achieved measurements.

## KNUST Capacity Model

Assumptions, not measurements:

- Launch campaign: 500 opens in five minutes, 200 registrations, 400 browsers, 100 carts, and 50 simultaneous checkouts.
- One open creates about 6-12 API requests; signup/auth sync creates about 3-6 first-party requests plus Clerk traffic.
- Practical launch target: roughly 40-80 first-party RPS and 50-150 concurrent sessions.
- 1,000 registrations over eight hours average 0.035/second, but a campaign/NAT burst can trigger Clerk per-IP limits.
- Planning tests for 10K/25K/50K total users should cover roughly 50/150/300 peak RPS. These are test targets, not capability claims.
- A 50-order burst creates at least 50 customer pushes plus owner/lifecycle messages. Expo's 600/second limit is above that scenario, but broker and receipt handling remain unproven.

**First likely bottleneck:** the declared 256 MB Postgres tier and single starter web/worker topology, followed by DB connections/query latency and queue age. Clerk campus-NAT limits may be the first visible signup bottleneck.

The monolith + PostgreSQL + Celery design is suitable for an early marketplace. The current deployment is not proven for launch; Kafka, Kubernetes, or microservices are not justified.

## Cost Model

Exact current cost is **BLOCKED - dashboard access required**.

| Provider | Verified basis | Scale implication |
|---|---|---|
| Render | 3 starter compute services, starter Key Value, basic-256mb Postgres declared | Sum live dashboard prices, storage, and egress; duplicate for isolated staging |
| Clerk | Hobby up to 50K MRU | Quota may fit early users; support/SLA needs review |
| Expo | $19 Starter; $199 Production | Free is not an operational production plan |
| Paystack | 1.95% of Ghana transaction value | Cost scales with GMV |
| Cloudinary | 25 free credits/month | Watch storage, transformations, bandwidth |
| Sentry | $0/$26/$80 tiers | Separate projects and alert quotas matter |
| Email | Provider/plan unknown | Inventory rate, bounce, and complaint limits |

At 1K, 10K, and 50K users, cost requires DAU, orders/user, image size, notifications, retention, current dashboard pricing, and GMV. Do not invent it.

## Code Findings Fixed

### Payment Integrity

- **PASS - tested:** Payment rows and stable references are reserved before Paystack initialization. Provider latency no longer holds a DB row lock.
- **PASS - tested:** Initialization timeout preserves one pending record and reuses the reference.
- **PASS - tested:** Refund state is reserved before Paystack. Indeterminate outcomes remain REFUND_PENDING, return 202, and are not blindly retried.
- **PASS - tested:** Signed unknown payment/refund/payout webhooks return 503 without permanently claiming the event.
- **PASS - statically verified:** Critical paths lock Order before Payment to reduce deadlock risk.
- **PASS - tested:** Incomplete provider success payloads return controlled 503.
- **PASS - tested:** Wrong amount/currency/metadata/signature and duplicate terminal events remain rejected/idempotent.

### Release, Monitoring, and Load Safety

- **PASS - tested:** Mobile validation reads production EAS values and catches shared Sentry, legal URLs, and OTA signing.
- **PASS - tested:** Expo SDK 54 patch alignment moved Expo Doctor from 17/18 to 18/18.
- **PASS - statically verified:** OTA signing activates only when certificate/key ID exist.
- **PASS - tested:** Owner Sentry captures request and global React errors, uses Next 16 client instrumentation, and has no fake DSN.
- **PASS - statically verified:** k6 defaults to localhost, blocks production, requires staging authorization, and selects one profile: smoke, baseline, load, spike, stress1000, soak, or burst.

## Verification Record

| Check | Result |
|---|---|
| Backend full pytest | PASS - tested: 732 passed, 1 skipped, 14 warnings |
| Payment-focused pytest | PASS - tested: 75 passed |
| Backend compile | PASS - tested |
| Mobile typecheck | PASS - tested |
| Mobile Jest | PASS - tested: 31 suites, 269 tests |
| Mobile lint | PASS - tested: 0 errors, 49 warnings |
| Expo Doctor | PASS - tested: 18/18 |
| Strict mobile release gate | FAILED: six blockers |
| Mobile production dependency audit | FAILED/PARTIAL: 23 advisories, mostly Expo/Metro build tooling; proposed fixes are incompatible |
| Owner typecheck | PASS - tested |
| Owner Vitest | PASS - tested: 4 files, 8 tests |
| Owner lint | PASS - tested: 0 errors, 181 warnings |
| Owner production build | BLOCKED/FAILED: native memory failure; Webpack build did not complete |
| Django production check --deploy | BLOCKED: no production env snapshot |
| Migration drift against production | NOT TESTED |
| Deployed OpenAPI compatibility | NOT TESTED |
| Load/stress/soak/burst | BLOCKED: tools absent and isolation unproven |
| Physical iOS/Android | NOT TESTED |
| Backup restore | NOT TESTED |
| Controlled Sentry alert | NOT TESTED |
| Paystack live reconciliation | NOT TESTED |

## Adversarial Flow Results

| Scenario | Evidence | Outcome |
|---|---|---|
| Repeated Place Order | PASS - tested/static | DB idempotency and constraints protect retries; live concurrency not run |
| Repeated Pay | PASS - tested | One pending Payment and stable reference reuse |
| App disappears during payment | PASS - static | Webhook/verify can reconcile; sandbox/physical interruption not run |
| Duplicate webhook | PASS - tested | Event claims and terminal states are idempotent |
| Webhook before local visibility | PASS - tested | Unknown reference returns 503 for retry |
| Refund timeout | PASS - tested | Pending/manual reconciliation; no blind duplicate |
| Paystack timeout/500/malformed | PASS - tested | Controlled retryable/indeterminate semantics |
| Wrong money/metadata | PASS - tested | Rejected before settlement |
| Database unavailable | PASS - static / NOT TESTED live | 503 expected; recovery not injected |
| Redis unavailable | PARTIAL | DB transactions survive; throttling/cache/queue degrade |
| Celery unavailable | PARTIAL | Primary writes survive; full replay/idempotency not proven |
| Clerk invalid token | PASS - tested | Fails closed; provider outage not tested |
| Push unavailable | PARTIAL | Orders survive; notifications can delay/fail |
| Push receipt error | FAILED | Normal delivery discards ticket-to-token mapping; receipt failures are not reconciled |
| Storage unavailable | PARTIAL | Controlled media errors exist; outage not injected |
| Two owners update | PASS - tested/static | Locks and transition rules exist; high concurrency not run |
| Slow/offline/killed mobile | PARTIAL | Recovery code/tests exist; physical matrix not run |

## Failure Matrix

| Component | User experience | Data risk | Recovery | Severity / action |
|---|---|---|---|---|
| Backend | 5xx/unavailable | Low if transaction absent | Render restart | P1 prove rollback/multi-instance |
| PostgreSQL | 503 expected | High if restore unproven | Provider/restore | P1 PITR + restore drill |
| Redis | Cache/throttle/queue degraded | Low orders, medium notifications | Reconnect | P1 stage outage |
| Celery | Delayed async work | Medium task side effects | Redelivery | P1 replay audit |
| Clerk | Auth/signup unavailable | Low corruption | Provider | P1 degraded UX/runbook |
| Paystack | Checkout unavailable/pending | High trust risk | Reconcile same reference | P1 interruption proof |
| Cloudinary | Media unavailable | Low-medium | Retry | P2 failure UX/quotas |
| Push | Delayed/lost | Low transaction risk | In-app refresh | P1 receipt persistence |
| Email | Delayed/lost | Low | Retry/resend | P2 bounce monitoring |
| Mobile network | Pending/retry UX | Duplicate risk | Reconnect/idempotency | P1 physical proof |
| Render region | Broad outage | High availability impact | Provider/restore | P1 runbook |
| DNS | API/legal links unavailable | No DB loss | DNS correction | P1 expiry monitoring |

## Backend-to-Mobile Gap Scan

Customer capabilities were mapped against mobile routes/services: addresses, sessions/deletion, referrals, laundries/favorites/reviews, orders, estimates, coupons, custom quotes, COD, online payment, receipts, tracking, notifications/preferences, support, feedback, and legal/settings have mobile surfaces or service calls.

No confirmed customer backend feature is wholly absent from mobile. Owner/admin functions correctly live in the owner portal/admin.

The key gap is operational: push receipt reconciliation exists only as a helper/diagnostic path and is not connected to normal delivery.

## Security, Privacy, and Stores

- **PASS - tested:** broad authorization, role, signature, money-tamper, idempotency, header, and redaction coverage.
- **PASS - statically verified:** Paystack secrets stay server-side.
- **PARTIAL:** cache-backed throttles fail open and are not strict abuse protection. Add gateway limits before launch.
- **FAILED:** production legal URLs are not Simame HTTPS URLs.
- **BLOCKED:** App Store privacy, Google Data Safety, SDK manifests, deletion web flow, and store metadata dashboards were not inspected.
- **BLOCKED:** no isolated staging dynamic IDOR/upload/rate red-team run.
- **P2:** track Expo/Metro advisories to compatible upstream fixes; do not force npm's incompatible downgrade.

## Database, Growth, and 12-Month Durability

The code uses UUID/unique references, FKs, pagination, transactions, row locks, and atomic counters in critical paths. That supports correctness, not measured scale.

Required staging data: 10K/50K users, 100K orders, 100K payments, 1M notifications, realistic audits/catalogue. Capture EXPLAIN ANALYZE for discovery, order lists, owner queues, notification history, settlements, and admin search. Monitor queries, p95 DB time, locks, deadlocks, and connections.

Most likely 12-month failures without maintenance:

1. DB/storage and unbounded notification/audit retention.
2. Pin, certificate, signing key, domain, and credential expiry.
3. Expo/React Native/Next/Django drift and advisories.
4. Silent queue growth or beat failure.
5. Receipt-level dead push tokens.
6. Sentry quota exhaustion/noisy alerts.
7. Restore procedures failing during an incident.
8. Price/catalogue changes racing with old carts.

## Load and Fault Execution

Neither k6 nor Locust is installed here. No remote test ran.

After isolation and synthetic data are documented, run each profile separately:

    k6 run -e TEST_PROFILE=smoke -e API_BASE_URL=https://connect-full-backend.onrender.com -e SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION scripts/loadtest_k6.js
    k6 run -e TEST_PROFILE=baseline -e API_BASE_URL=https://connect-full-backend.onrender.com -e SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION scripts/loadtest_k6.js
    k6 run -e TEST_PROFILE=load -e API_BASE_URL=https://connect-full-backend.onrender.com -e SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION scripts/loadtest_k6.js
    k6 run -e TEST_PROFILE=spike -e API_BASE_URL=https://connect-full-backend.onrender.com -e SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION scripts/loadtest_k6.js
    k6 run -e TEST_PROFILE=stress1000 -e API_BASE_URL=https://connect-full-backend.onrender.com -e SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION scripts/loadtest_k6.js
    k6 run -e TEST_PROFILE=soak -e API_BASE_URL=https://connect-full-backend.onrender.com -e SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION scripts/loadtest_k6.js
    k6 run -e TEST_PROFILE=burst -e API_BASE_URL=https://connect-full-backend.onrender.com -e SIMAME_LOAD_TEST_ACK=I_HAVE_STAGING_AUTHORIZATION scripts/loadtest_k6.js

Enable Render CPU/memory/latency, Postgres connections/query/locks, Key Value memory/eviction, Celery queue depth/age, and Sentry errors. Abort at 5% errors, DB 85% connections, sustained queue growth, payment mismatch, duplicates, or cross-user exposure.

The current k6 flow is mainly read/health/estimate. Add authenticated fixture-driven signup/order/owner journeys before using it for certification. Never target production.

## Exact Manual Actions

1. **Render:** identify/provision separate staging and production projects, DBs, Key Value, Cloudinary, web, worker, beat, and secrets. Resolve both degraded readiness states with the internal health token.
2. **Database:** confirm paid PITR, restore into isolation, run integrity counts, record duration, and boot the app against the restore.
3. **Sentry:** separate staging/production projects, update DSNs, configure 5xx/payment/auth/queue alerts, and receive controlled errors from API, worker, mobile, and owner.
4. **Legal:** publish working https://simame.tech privacy, terms, and deletion pages; update EAS and store consoles.
5. **EAS:** create signing certificate/key per Expo, keep private key out of Git, configure certificate/key ID in protected secrets, and pass the strict gate.
6. **Paystack:** confirm live key mode, webhook URL, retries, settlement account, and callback. Run test-mode interruption cases, then one approved small live transaction/refund with reconciliation evidence.
7. **Clerk:** record instance IDs, issuer/audience/JWKS, webhook secrets, custom domain, and campus-NAT behavior.
8. **CI/build:** complete a clean owner build with adequate memory; build signed iOS/Android RCs. Expo Go is not proof.
9. **Devices:** run the customer-owner journey on physical iPhone/Android under normal, slow, interrupted, killed/reopened, and cold-push conditions.
10. **Faults:** in isolated staging, stop DB, Redis, worker, and beat separately; inject provider timeout/500/malformed responses; capture UX, recovery, duplicates, queues, and reconciliation.

## Twenty Required Answers

1. **Can real customers use it safely today?** No; release, live health, device, and recovery evidence is incomplete.
2. **Can real laundries onboard today?** Not for unrestricted launch; controlled internal onboarding only after environment/legal checks.
3. **Can real money move safely?** Code is hardened; live/sandbox interruption and reconciliation proof remains.
4. **1,000 signups/day?** Average is modest; a campus NAT burst may hit Clerk per-IP limits. Not load-tested.
5. **Hundreds open simultaneously?** Plausible architecture; capacity unmeasured and small DB likely bottleneck.
6. **Internet disappears during payment?** Durable reference and webhook/verify should reconcile; physical/sandbox proof absent.
7. **Repeated Place Order?** DB idempotency should replay one result; large concurrent proof pending.
8. **Database offline?** 503 should occur; recovery was not injected/timed.
9. **Redis/Celery fails?** Orders/payments survive; throttles/cache/push/email/campaigns degrade.
10. **Paystack unavailable?** Controlled 503/retry; refund uncertainty remains pending.
11. **Clerk unavailable?** New auth/signup fails closed; provider recovery governs availability.
12. **Push fails?** Orders survive; notification can be lost/delayed and receipts are incomplete.
13. **Render restarts?** Stateless processes should restart; queue/startup/RTO behavior untested.
14. **Current maximum proven load?** **NOT ESTABLISHED: zero authorized load-test RPS/concurrency measured.**
15. **What breaks first?** Small Postgres/web/worker or Clerk campus-NAT signup limit.
16. **Architecture sufficient for KNUST?** Pattern yes; deployment/evidence no.
17. **Before Kumasi-wide scaling?** Measure, right-size DB/compute, add pooling/autoscaling if justified, persist receipts, automate capacity/DR.
18. **Before Ghana-wide scaling?** Multi-instance services, tested HA/restore, provider incident/fraud/support/reconciliation operations, mature data lifecycle.
19. **Likely within 12 months?** Credentials, dependencies, growth, queues/beat, push tokens, monitoring quotas, and restore drills.
20. **Maintenance?** Weekly alerts/queues/payments; monthly capacity/cost/dependencies/backups; quarterly restore/fault/device/security; every-release legal/store/gates; annual expiry audit.

## Readiness Score

| Area | Score |
|---|---:|
| Architecture | 78 |
| Backend | 88 |
| Mobile | 84 |
| Database | 62 |
| Payments | 86 |
| Authentication | 75 |
| Order integrity | 90 |
| Concurrency safety | 84 |
| Network resilience | 70 |
| Scalability | 35 |
| Security | 78 |
| Privacy | 52 |
| Notifications | 58 |
| Background jobs | 63 |
| Observability | 60 |
| Backups | 25 |
| Disaster recovery | 20 |
| Deployment safety | 48 |
| iOS | 35 |
| Android | 35 |

**Overall evidence-weighted readiness: 61/100.**

**PROVEN CAPACITY:** Not established; no authorized staging load ran.  
**ESTIMATED CAPACITY:** Launch target is roughly 40-80 peak first-party RPS and 50-150 concurrent sessions. This is not measured capability.

## Blockers

### P0 - STOP LAUNCH

No unresolved code-level P0 was confirmed after payment changes. Launch remains stopped by P1 evidence/control failures.

### P1 - MUST FIX BEFORE REAL USERS

- Resolve both degraded live readiness endpoints.
- Prove complete staging/production provider and data isolation.
- Clear all six strict mobile gate failures.
- Complete realistic staging signup/load/spike/stress/soak/burst.
- Complete Paystack interruption/reconciliation and approved live proof.
- Persist/map/check normal Expo push receipts.
- Complete physical iOS/Android RC journeys.
- Complete/timestamp DB restore and deployment rollback.
- Demonstrate alerts and controlled Sentry ingestion.
- Complete a clean owner production build in CI.
- Produce an immutable cross-repository release manifest.

### P2 - STRONGLY RECOMMENDED SOON

- Reduce mobile/owner lint and broad any debt.
- Track Expo/Metro advisories to compatible upstream fixes.
- Add authenticated fixture load flows and large-data query plans.
- Define notification/audit/analytics retention.
- Add gateway abuse limits independent of Redis-backed throttles.
- Inventory email quotas/bounces and Cloudinary upload limits.

## Final Verdict

**🔴 NO-GO**

