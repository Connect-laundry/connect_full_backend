# Connect Laundry Production Integration & Architecture Audit

Audit date: 2026-05-20  
Scope: `connect-customer-mobile` Expo React Native app and `connect_new_backend` Django/DRF backend  
Audit mode: static code scan plus local validation. No product code was changed.

## Verification Performed

| Area | Command | Result |
| --- | --- | --- |
| Backend tests | `python -m pytest` in `connect_new_backend` | 61 passed, 2 warnings |
| Frontend TypeScript | `npm.cmd run typecheck` in `connect-customer-mobile` | Passed |
| Frontend lint | `npm.cmd run lint` in `connect-customer-mobile` | Passed with 1 warning in `app/+not-found.tsx` |
| Frontend release config | `npm.cmd run validate:release` | Passed, but warned that release env values were absent in this shell |

## Architecture Map

### Backend Surface

| Domain | Backend implementation |
| --- | --- |
| Auth/session | JWT auth, rotating refresh tokens, token blacklist, device sessions, logout, logout-all, session revoke, account deletion/deactivation, forgot/reset password |
| Users/profile | `GET/PATCH /auth/me/`, address CRUD, supported cities, media upload, referral apply/stats |
| Laundries | Public list/detail/categories/featured/favorites/reviews, owner dashboard stats/earnings/orders, owner service management, admin laundry/service approval paths |
| Booking/orders | Catalog, schedule slots, calculate/estimate, create order with payment intent, customer order list/detail/active, lifecycle transitions, coupon validation |
| Payments | Paystack initialize/verify/webhook, HMAC webhook validation, replay protection, amount/currency/metadata validation |
| Logistics | Delivery assignment CRUD for admin/owner/driver visibility, tracking log read endpoint |
| Marketplace/support | FAQs, feedback, legal pages, special offers, DB notifications with unread/mark-read actions |
| Ops/security | Standard response renderer, JSON exception envelope, request ID middleware, POST idempotency middleware, security headers, health check, Sentry redaction, Celery/Redis |

### Frontend Surface

| Domain | Frontend implementation |
| --- | --- |
| Auth/session | Login/signup/forgot/reset/logout, token refresh queue, device headers, local secure storage, account deletion |
| Customer app | Home, basket/discovery, laundry details, booking, checkout, orders, maps, tracking, notifications, support, referral, settings |
| State | React Query, context for user/cart/theme, SecureStore for tokens/user/favorites/recent views |
| Payments | Checkout creates booking and opens Paystack URL; verifies once after browser close |
| Maps/location | Foreground location, Mapbox geocoding in checkout, Google Directions API in maps |
| Security | Runtime checks, SSL pinning hooks, app integrity hook, Sentry redaction, request IDs, idempotency headers |

## Launch Readiness Verdict

Current score: **72 / 100**

Backend core security and payment webhook hardening are strong enough to build on, and the backend test suite is currently green. The mobile app also typechecks and lints. The ecosystem is not production-synchronized yet because several mobile screens call wrong contracts, render backend fields incorrectly, expose dead flows, or promise realtime/logistics behavior that the backend does not currently provide.

## Critical Findings

| ID | Finding | Evidence | Impact | Required fix |
| --- | --- | --- | --- | --- |
| C1 | Notification contract is broken. Backend sends `body` and UUID `id`; frontend expects numeric `id` and `message`, then calls `notification.message.match(...)`. Mark-read uses POST while backend action is PATCH. | `connect_new_backend/marketplace/serializers.py`; `connect_new_backend/marketplace/views/notifications.py`; `connect-customer-mobile/src/services/support.service.ts`; `connect-customer-mobile/src/features/notifications/screens/NotificationsScreen.tsx` | Notifications can render blank text, crash on press, and fail to mark read. | Normalize `body -> message`, type `id` as string, use `related_order` for navigation, and change mark-read to PATCH. |
| C2 | Address CRUD contract is wrong. Backend address fields are `label`, `address_line1`, `city`, `latitude`, `longitude`, `is_default`; frontend creates `name`, `street`, `apartment`, `region`, `postal_code`, `country`, `phone`, `type`. | `connect_new_backend/users/serializers/profile.py`; `connect-customer-mobile/src/api/types.ts`; `connect-customer-mobile/src/features/schedule/hooks/useAddressManagement.ts` | Saved addresses fail validation or render as `undefined`; schedule/address UX is not production-backed. | Align DTOs and UI mapping to backend fields, or expand backend serializer intentionally. |
| C3 | Booking catalog paths are wrong in mobile. Frontend calls `/booking/catalog/services/` and `/booking/catalog/items/`; backend exposes `/booking/services/` and `/booking/items/`. | `connect-customer-mobile/src/api/endpoints.ts`; `connect_new_backend/ordering/urls.py` | Schedule/global booking data queries fail. | Update endpoint constants or add backend compatibility aliases. |
| C4 | Realtime tracking is not actually implemented end-to-end. Backend exposes read-only tracking logs; no mobile driver app or driver coordinate write endpoint exists. Frontend polls every 10s and shows a driver card UI that is never populated. | `connect_new_backend/logistics/views.py`; `connect-customer-mobile/src/features/tracking/hooks/useTracking.ts`; `connect-customer-mobile/src/features/tracking/RealTimeTrackingScreen.tsx` | The product promises live tracking but can only display existing logs if something else creates them. | Add driver/mobile assignment + location write flow, or reframe as lifecycle tracking only. |
| C5 | Map markers can be synthetic. Laundry list serializer does not include `latitude`/`longitude`, but the map tab uses list data and falls back every laundry to Accra coordinates plus invented prices/hours. | `connect_new_backend/laundries/serializers/laundry_list.py`; `connect-customer-mobile/src/features/maps/hooks/useLaundryData.ts` | Customers may see false locations, route to wrong places, and make bad ordering decisions. | Include coordinates in list/map endpoint or fetch detail for map records; remove fallback coordinates/prices/hours. |

## High Findings

| ID | Finding | Evidence | Impact | Required fix |
| --- | --- | --- | --- | --- |
| H1 | Payment reconciliation is incomplete. Checkout clears cart immediately after order creation, opens Paystack, then verifies exactly once after browser close. There is no callback/deep-link handling, retry payment, existing pending payment initialize, or receipt reconciliation action. | `connect-customer-mobile/src/features/checkout/CheckoutReviewScreen.tsx`; `connect-customer-mobile/src/services/payment.service.ts`; `connect_new_backend/payments/views.py` | Valid payments may appear pending; failed starts create orders with no recovery path. | Add payment callback/deep link, receipt retry/verify action, and safe initialize/update handling for existing pending payments. |
| H2 | `/payments/initialize/` backend feature is unused by mobile. | `connect_new_backend/payments/urls.py`; `connect-customer-mobile/src/api/endpoints.ts`; `connect-customer-mobile/src/services/payment.service.ts` | Users cannot recover a pending Paystack order from receipt. | Implement initialize/retry flow for unpaid pending orders. |
| H3 | Orders UI uses stale statuses `WASHING` and `READY`; backend statuses are `CONFIRMED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`, etc. | `connect_new_backend/ordering/models/base.py`; `connect-customer-mobile/src/features/orders/OrdersScreen.tsx`; `connect-customer-mobile/src/features/orders/constants/orderTabs.ts` | Filters are misleading and active/completed grouping is wrong. | Replace frontend status model with backend enum and map user-friendly labels. |
| H4 | The standalone `ScheduleScreen` posts an unsupported booking shape: `pickup_address_id`, recurring fields, no `items`. | `connect-customer-mobile/src/features/schedule/ScheduleScreen.tsx`; `connect_new_backend/ordering/serializers/order.py` | Direct route/deep link cannot create a real order. | Remove this route or rewire it to the checkout cart/order contract. |
| H5 | Runtime HTTPS enforcement is advisory by bug. `http.ts` catches the cleartext rejection and continues instead of blocking. | `connect-customer-mobile/src/services/http.ts` | Production may not actually block non-HTTPS API URLs despite security config. | Rethrow the HTTPS validation failure before creating requests. |
| H6 | Driver lifecycle permission contains stale role `RIDER` while user roles define `DRIVER`. | `connect_new_backend/ordering/permissions.py`; `connect_new_backend/users/models.py` | Driver users cannot perform lifecycle actions the comments say they can. | Replace `RIDER` with `DRIVER` and add/keep permission tests. |
| H7 | Render production config omits critical env vars: Paystack keys/callback, Cloudinary, email, CORS/CSRF production origins, Sentry, frontend URL, internal health token, payment currency. | `connect_new_backend/render.yaml`; `connect_new_backend/.env.example`; `connect_new_backend/config/settings.py` | Deployed backend can boot without real payment/email/media/observability configuration. | Add required env var groups/secrets to Render config or deployment runbook with fail-fast checks. |
| H8 | Release validation warns required mobile env is absent: API URL, maps, Mapbox, Sentry, privacy/legal links, SSL pins, EAS updates URL and code signing. | `connect-customer-mobile/scripts/validate-release-config.mjs`; `npm.cmd run validate:release` output | Production build can ship with missing maps, unsigned OTA, no Sentry, stale/default pins, or dead policy links. | Run strict env validation in CI/EAS and populate secrets. |

## Medium Findings

| ID | Finding | Evidence | Impact | Required fix |
| --- | --- | --- | --- | --- |
| M1 | Frontend has services for active sessions/revoke-all but no account settings UI for device session management. | `connect-customer-mobile/src/services/auth.service.ts`; `connect-customer-mobile/src/features/settings/screens/AccountSettings.tsx` | Backend session model is not visible to users. | Add device sessions screen and revoke actions. |
| M2 | Referral apply endpoint exists but mobile only shows stats/share; no apply-code flow. | `connect_new_backend/users/views/referral.py`; `connect-customer-mobile/src/features/referral/screens/ReferralScreen.tsx` | Backend referral acquisition is not used in app. | Add apply referral code during signup/onboarding or account screen. |
| M3 | Coupon validation backend exists but checkout/cart promo flow is not wired to it. | `connect_new_backend/ordering/views/order_views.py`; `connect-customer-mobile/src/context/CartContext.tsx` | Discounts are local state or absent from final order, not server-validated UX. | Add coupon validate call and send `coupon_code` only after backend acceptance. |
| M4 | Featured laundries endpoint exists, but frontend uses `recommended=true` for featured. | `connect_new_backend/laundries/views/laundry.py`; `connect-customer-mobile/src/services/laundry.service.ts`; `connect-customer-mobile/src/features/home/services/home.service.ts` | Admin-featured merchandising is not honored. | Use `/laundries/featured/` or `featured=true/is_featured=true`. |
| M5 | Supported cities response is `{status,cities}`, but mobile expects `data` array and may return empty for valid responses. | `connect_new_backend/users/views/profile.py`; `connect-customer-mobile/src/features/basket/services/basket.service.ts` | City filters can silently fail. | Parse `cities` top-level. |
| M6 | Laundry category filter likely references a non-existent relation `services__category`. | `connect_new_backend/laundries/filters.py`; `connect_new_backend/laundries/models/service.py` | Backend category filters can fail or return incorrect results. | Point filter to `laundry_services__item__item_category` or intended relation. |
| M7 | Review endpoint accepts reviews without verified completed order evidence. | `connect_new_backend/laundries/views/review.py` | Anyone authenticated can review any laundry. | Enforce completed-order ownership before review creation. |
| M8 | Push notifications are not implemented. Backend creates DB notifications; task `send_real_push` is placeholder; frontend has no Expo push token registration. | `connect_new_backend/marketplace/tasks.py`; `connect-customer-mobile/package.json`; notification services | Notification UX is pull-only despite mobile expectations. | Add push token model, registration API, Expo/FCM send task, opt-in preferences. |
| M9 | Backend owner/admin dashboard and laundry/service approval APIs have no mobile or web frontend in this repo. | `connect_new_backend/laundries/urls.py`; frontend route tree | Operational backend features are not productized for owners/admins. | Confirm separate admin web app exists; otherwise build owner/admin surfaces. |
| M10 | Account settings sends `address` in profile FormData, but profile serializer does not accept an address field. Email is read-only backend-side but editable UI-side. | `connect-customer-mobile/src/features/settings/screens/AccountSettings.tsx`; `connect_new_backend/users/serializers/profile.py` | Profile update can fail or mislead users. | Remove unsupported fields or add real backend support. |

## Low / Informational Findings

| ID | Finding | Evidence | Recommendation |
| --- | --- | --- | --- |
| L1 | Lint has one unused `isDarkMode` warning. | `npm.cmd run lint` | Clean before release. |
| L2 | Backend schema/docs are only exposed in DEBUG. | `connect_new_backend/config/urls.py` | Keep this for production; generate contract artifacts in CI instead. |
| L3 | Frontend direct Google Directions and Mapbox geocoding keys are public by design. | `connect-customer-mobile/src/config/env.ts`; `useMapDirections.ts`; checkout location picker | Require provider-side app restrictions and quota alerts. |
| L4 | Current SSL pin fallback expiration is 2026-06-26, close to the audit date. | `connect-customer-mobile/src/config/env.ts` | Rotate and inject release pins via env before store submission. |

## API Contract Mismatch Matrix

| Endpoint / feature | Backend contract | Frontend expectation | Severity |
| --- | --- | --- | --- |
| `GET /support/notifications/` | `body`, UUID `id`, `related_order` | `message`, numeric `id`, parse order from `#...` text | Critical |
| `PATCH /support/notifications/{id}/mark-read/` | PATCH | POST | Critical |
| `/addresses/` | `label`, `address_line1`, `city`, `latitude`, `longitude` | `name`, `street`, `region`, `postal_code`, `type`, `phone` | Critical |
| `/booking/services/`, `/booking/items/` | Actual backend paths | `/booking/catalog/services/`, `/booking/catalog/items/` | Critical |
| `GET /addresses/supported-cities/` | `{status:"success", cities:[...]}` | array or `data` array | Medium |
| Laundry list | No `latitude`, `longitude`, `services`, `categories`, `phone` | Map/search filters expect them | Critical |
| Order statuses | `PENDING`, `CONFIRMED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`, `REJECTED`, `CANCELLED` | `WASHING`, `READY`, incomplete grouping | High |
| Payment retry | Backend has initialize/verify/webhook | Mobile only verify after initial browser close | High |
| Session management | Backend has sessions/revoke-current/revoke-all/logout-all | Service exists but no UI | Medium |
| Coupon validation | Backend has `/orders/coupons/validate/` through router | No endpoint constant/checkout integration | Medium |

## Backend Features Missing in Frontend

- Device session list/revoke-current/revoke-all UI.
- Logout-all-devices UX.
- Referral apply flow.
- Coupon validate/list UX.
- Payment initialize/retry after pending payment.
- Owner dashboard stats, earnings, order management.
- Laundry owner service availability/pricing management.
- Admin laundry approval/rejection and service approval operations.
- Delivery assignment management.
- Driver assignment and lifecycle operations.
- Push token registration and notification preferences.
- Backend legal endpoint consumption by privacy/terms screens.
- Media upload endpoint usage for avatars, if profile image upload is intended.

## Frontend Features Missing Backend Support

- Live driver location tracking from mobile.
- Driver profile/card/chat/call data in tracking screen.
- Standalone pickup scheduling route with recurring schedule and `pickup_address_id`.
- Map list markers from laundry list coordinates.
- In-app notification navigation by parsing message text.
- Local basket promo/discount state without coupon backend integration.
- Synthetic map prices/hours/delivery labels.
- Referral history and leaderboard screens.

## Security Mismatch Report

- Strong backend: JWT rotation/blacklist, device sessions, deactivation middleware, POST idempotency, request IDs, webhook HMAC/replay/amount checks, Sentry redaction.
- Strong frontend: SecureStore for auth/user/favorites, refresh queue, device headers, runtime checks, Sentry redaction, idempotency headers.
- Gaps: HTTPS blocking bug, release env not strict, app integrity is advisory in production, SSL pins expire soon, push/security telemetry is not fully wired, owner/admin/driver frontend role surfaces do not exist, review authorization is too permissive.

## Payment Readiness

Backend Paystack webhook handling is production-oriented. Mobile payment UX is not launch-ready because it lacks callback/deep-link reconciliation, retry initialization, receipt verification controls, and clear pending/failed recovery states. Backend also updates order status directly on payment success instead of going through the order state machine/history, so operational audit trails can be incomplete.

## Order & Logistics Readiness

Customer order creation through checkout is mostly aligned when using `CheckoutReviewScreen`. The old `ScheduleScreen` is not aligned. Backend lifecycle state is richer than the mobile UI. Logistics has assignment and read-only tracking logs, but no real driver coordinate pipeline.

## Notification & Realtime Readiness

DB notification support exists, but the mobile contract is broken and push is absent. There is no websocket/realtime transport in either repo. Polling is present for tracking, not for notifications except user refresh.

## Maps & Location Readiness

Foreground permission usage is appropriate. Checkout geocoding and maps directions have timeout/retry handling. The biggest blocker is data truth: list laundries do not include coordinates, and map code falls back to fixed Accra coordinates and invented details.

## State Management & Consistency

React Query policy is reasonable. High-risk spots are optimistic favorites across local context and backend cache, cart clearing before payment confirmation, stale active order polling every 10s with no app-state pause, and unsupported filters that make local UI diverge from backend results.

## Production UX Gap Report

- Notifications can crash and fail mark-read.
- Payment pending recovery is weak.
- Order statuses and filters are stale.
- Direct schedule route is dead/unsafe.
- Maps can show false coordinates/details.
- Account settings exposes unsupported profile fields.
- Device sessions are invisible despite backend support.
- Several routes are customer-only while backend has owner/admin/driver domains.

## Performance Bottlenecks

- Laundry list `isFavorite` performs per-row favorite existence checks; can become N+1 under pagination.
- Tracking polls every 10 seconds without app foreground/backoff logic.
- Map screen would need detail fetches for coordinates if list serializer is not expanded.
- Client-side filters do extra work because several backend filter params are unsupported.

## Operational Risk Report

- Render config lacks many production secrets and integration env values.
- Frontend release validation is non-strict by default and currently reports missing env in this shell.
- No CI evidence in this audit for strict mobile env validation.
- Paystack callback URL in `.env.example` points to `/payments/callback/`, but implemented backend webhook path is `/payments/webhook/`; Paystack frontend callback/deep link is not implemented.
- Admin monitoring viewset exists but is not registered in marketplace URLs.

## Technical Debt / Dead Code

| Area | Category |
| --- | --- |
| `src/features/schedule/ScheduleScreen.tsx` | Risky removal or rewrite; direct route exists but unsupported booking contract |
| `src/features/maps/data/map.mock.ts` and map fallbacks | Safe removal after real list coordinates exist |
| Referral history/leaderboard components | Requires backend migration or hide as unavailable |
| `AuthService.getActiveSessions` without UI | Implement UI rather than remove |
| Lifecycle endpoint constants in mobile | Risky removal; may be needed for owner/driver app, but unused in customer app |
| `marketplace/views/admin_monitoring.py` | Requires URL registration or removal |
| `ordering/views/promo.py` stale import path | Safe cleanup after confirming no imports |

## Red-Team Product Audit

| Abuse case | Current posture | Gap |
| --- | --- | --- |
| Duplicate order submission | Frontend sends idempotency headers; backend caches successful POSTs | Only POST is protected; cart clears before confirmed payment |
| Duplicate payment webhook | Backend replay table and success idempotency | Mobile lacks reconciliation UI |
| Amount/currency tampering | Backend validates Paystack amount/currency/metadata | Good backend posture |
| Unauthorized order access | Customer order viewset filters by user; logistics filters visible orders | Owner/admin order access through customer order endpoint is not supported |
| Driver privilege abuse | Backend assignment visibility exists | Lifecycle permission uses `RIDER`, not `DRIVER` |
| Stale refresh token replay | Backend session refresh token tracking and reuse handling exists | Mobile has no user-facing session review/revoke UI |
| Webhook spoofing | HMAC validation present | Requires real Paystack secret in deployment |
| Review abuse | Authenticated user can review laundry without completed-order proof | Needs server-side purchase verification |
| Offline replay | Idempotency helps POST duplicates with stable body/key | Frontend does not present offline queue; avoid adding offline mutation replay without server design |

## Recommended Fix Order

1. Fix notification DTO + PATCH mark-read.
2. Fix address DTOs and remove unsupported profile `address`/editable email behavior.
3. Fix booking catalog endpoint constants.
4. Replace order status model in UI with backend enum.
5. Remove/rebuild dead `ScheduleScreen`.
6. Remove synthetic map coordinates/details; add backend list coordinates or a map endpoint.
7. Add payment retry/reconciliation flow using initialize + verify + webhook-authoritative state.
8. Fix HTTPS enforcement and rotate SSL pins.
9. Add device session management UI.
10. Decide owner/admin/driver product surface strategy and either build it or document it as out-of-scope.
11. Add push token registration and real push sending.
12. Harden deployment env with strict CI/EAS checks and Render secret coverage.

