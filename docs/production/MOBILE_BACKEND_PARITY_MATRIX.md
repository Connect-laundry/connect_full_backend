# Simame Mobile <-> Backend Parity Matrix

Audit date: 2026-08-13

## Scope And Method

The inventory was generated from Django's URL resolver and DRF router, then compared with the regenerated `docs/api/simame-openapi.yaml`. The validated OpenAPI contract contains 174 paths. All 174 documented parameter-normalized paths resolve in Django. The resolver additionally exposes three intentional non-schema operations: `GET /api/v1/payments/callback/`, `POST /api/v1/payments/webhook/`, and `POST /api/v1/payments/paystack/webhook/`, plus router roots and format-suffix variants.

Customer scope contains 84 paths / 95 HTTP operations when compatibility aliases are counted. Sixteen operations under `/api/v1/booking/*` duplicate canonical `/api/v1/orders/*` behavior. Owner, driver, staff, webhook and admin operations are listed separately and are not mobile omissions.

A `COMPLETE` row means the endpoint is represented by a service call and a reachable customer workflow, not merely a constant. Loading/error columns cover the consuming workflow as a whole. `Partial` details are named explicitly.

## Matrix

| Method | Endpoint(s) | Domain | Auth / role | Request schema | Important response fields | Backend evidence | Mobile API | State / UI evidence | Reachable | Loading | Errors | Tests | Status | Severity | Remediation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| GET, POST | `/addresses/` | Addresses | JWT / customer | address fields on POST | id, label, coordinates, default | `users/views/address.py`; serializers | `ENDPOINTS.ADDRESSES`; address service | checkout location and address screens | YES | YES | YES | YES | COMPLETE | P1 | None |
| GET, PUT, PATCH, DELETE | `/addresses/{id}/` | Addresses | JWT / owner of address | partial/full address | updated address / 204 | same | address service | account and checkout address actions | YES | YES | YES | YES | COMPLETE | P1 | None |
| GET | `/addresses/supported-cities/` | Service area | Public/customer | none | city/service coverage | `users/views/address.py` | address service | location validation | YES | YES | YES | YES | COMPLETE | P2 | None |
| POST | `/analytics/events/` | Product analytics | JWT/customer | event batch | accepted count | `analytics/views.py` | analytics service | background event queue | YES | N/A | YES | YES | COMPLETE | P2 | Dashboard verification remains manual |
| DELETE | `/auth/account/` | Account deletion | JWT/customer | optional reason | 204 | `users/views/profile.py`; `users/services/account_deletion.py`; Clerk service | `AuthService.deleteAccount` | Settings -> Account Settings -> Delete Account, destructive confirmation | YES | YES | YES | YES | COMPLETE | P0 | Deploy and verify against production Clerk; publish external deletion page |
| POST | `/auth/login/`, `/register/`, `/social-login/`, `/token/refresh/`, `/logout/` | Authentication | Public or JWT/customer | credentials/provider token/refresh token | tokens, user, expiry | `users/views/auth.py`; Clerk auth | auth service | sign-in, sign-up, session bootstrap/logout | YES | YES | YES | YES | COMPLETE | P0 | Replace test Clerk production profile values |
| GET, PUT, PATCH | `/auth/me/` | Profile | JWT/customer | profile update | current user/profile | `users/views/profile.py` | auth/profile service | profile and settings | YES | YES | YES | YES | COMPLETE | P1 | None |
| POST | `/auth/forgot-password/`, `/reset-password/` | Credential recovery | Public | email; token/new password | neutral success | `users/views/password_reset.py` | auth service | forgot/reset screens | YES | YES | YES | YES | COMPLETE | P1 | Verify production email delivery manually |
| GET, POST | `/auth/sessions/`, `/auth/sessions/revoke-current/`, `/auth/sessions/revoke-all/` | Session control | JWT/customer | revoke request | device sessions / success | `users/views/session.py` | auth-device service | Settings security/session controls | YES | YES | YES | YES | COMPLETE | P1 | None |
| GET, POST | `/auth/session/`, `/auth/logout-all/` | Compatibility auth | JWT/customer | none | session/logout result | auth URLs/views | no direct call; canonical session endpoints used | no separate UI needed | NO | N/A | N/A | YES | NOT_APPLICABLE | P3 | Retain compatibility or deprecate with telemetry |
| GET, POST, PATCH | `/booking/*` customer operations | Deprecated booking alias | same as orders/customer | same as canonical order route | same as canonical route | `ordering/urls.py` duplicate mount | deliberately absent | canonical `/orders/*` UI | NO | N/A | N/A | YES | NOT_APPLICABLE | P3 | Keep only for installed legacy clients; document removal window |
| GET, POST | `/orders/` | Order list/create | JWT/customer | order create on POST | paginated orders and payment fields | `ordering/views/order_views.py`; serializers | OrderService | Orders screen and checkout | YES | YES | YES | YES | COMPLETE | P0 | Generic update/delete are now blocked server-side |
| GET | `/orders/{id}/`, `/active/` | Order detail | JWT/customer | id/query | order, lifecycle, payment state | same | OrderService | Orders and receipt/detail screens | YES | YES | YES | YES | COMPLETE | P0 | None |
| POST | `/orders/create/`, `/estimate/` | Booking/price | JWT/customer | laundry, services/items, addresses, schedule | authoritative totals, currency, order | ordering create/estimate views | BookingService | booking and checkout review | YES | YES | YES | YES | COMPLETE | P0 | Backend remains price authority |
| GET | `/orders/items/`, `/services/`, `/schedule/` | Booking catalog | Public/customer | laundry/date filters | catalog, availability | ordering views | BookingService | booking flow | YES | YES | YES | YES | COMPLETE | P1 | None |
| POST | `/orders/coupons/validate/` | Promotions | JWT/customer | code, laundry, amount | validity, discount | ordering coupon view | OrderService | checkout coupon action | YES | YES | YES | YES | COMPLETE | P0 | None |
| POST | `/orders/calculate/` | Compatibility estimate | JWT/customer | order inputs | calculated totals | ordering views | no direct call; `/estimate/` used | canonical estimate UI | NO | N/A | N/A | YES | NOT_APPLICABLE | P3 | Deprecate after client telemetry confirms no use |
| GET | `/orders/{id}/price-breakdown/`, `/tracking/`, `/lifecycle/{id}/timeline/` | Price/tracking | JWT/customer owner | id | authoritative fees, status/timeline | ordering views | OrderService/logistics service | receipt and tracking screens | YES | YES | YES | YES | COMPLETE | P0 | None |
| PATCH | `/orders/lifecycle/{id}/cancel/` | Cancellation | JWT/customer owner | reason | new status/refund policy result | `ordering/views/lifecycle.py` | lifecycle service | order detail cancellation action | YES | YES | YES | YES | COMPLETE | P0 | UI now mirrors backend payment/status cancellation gate |
| GET | `/laundries/laundries/`, `/{id}/`, `/featured/`, `/laundries/featured/` | Discovery | Public/customer | location/filter/pagination | laundry, distance, status | laundries views/serializers | Laundry service | home, discovery, map, detail | YES | YES | YES | YES | COMPLETE | P1 | One featured route is an alias |
| GET | `/laundries/categories/`, `/categories/{id}/`, `/laundries/{id}/services/` | Catalog | Public/customer | id/filter | categories and price menu | laundries views | Laundry/booking services | discovery/detail/booking | YES | YES | YES | YES | COMPLETE | P1 | Detail category is consumed through list data; no separate screen required |
| GET, POST | `/laundries/favorites/`, `/laundries/{id}/favorite/` | Favorites | JWT/customer | laundry id | favorite state/list | laundries favorite views | Laundry service | discovery/detail favorites | YES | YES | YES | YES | COMPLETE | P2 | None |
| POST | `/laundries/{laundry_id}/reviews/` | Reviews | JWT/customer with eligible order | rating/comment | review | laundries review view | review service | completed-order rating flow | YES | YES | YES | YES | COMPLETE | P1 | None |
| GET, POST | `/legal/`, `/legal/{slug}/`, `/legal/current-versions/`, `/legal/user-acceptance/` | Legal consent | mixed; acceptance JWT | slug/acceptance versions | published text/versions/acceptance | marketplace legal views | Legal service | onboarding/settings legal screens | YES | YES | YES | YES | COMPLETE | P0 | Business/legal must verify final published content |
| GET | `/logistics/tracking/`, `/tracking/{id}/` | Logistics | JWT/customer owner | order/assignment query | coordinates/status/ETA | logistics views | Logistics service | order tracking map | YES | YES | YES | YES | COMPLETE | P1 | Device-route acceptance test required |
| POST | `/media/upload/` | Media | JWT/customer | multipart image | URL/metadata | media upload view | profile upload service | profile avatar action | YES | YES | YES | YES | COMPLETE | P1 | Production storage policy/manual retention verification required |
| POST, GET | `/payments/initialize/`, `/verify/{reference}/`, `/status/{reference}/`, `/receipt/{reference}/` | Paystack | JWT/customer owner | order id/reference | authorization URL, provider state, receipt | `payments/views.py`; services/webhooks | PaymentService | checkout browser/deep link, order receipt | YES | YES | YES | YES | COMPLETE | P0 | Live charge/refund and callback test are manual |
| POST, GET | `/referral/apply/`, `/referral/stats/` | Referrals | JWT/customer | referral code | application/stats | referral views | referral service | referral settings/screens | YES | YES | YES | YES | COMPLETE | P2 | None |
| GET | `/support/faqs/`, `/support/help/faq/` | Help | Public | optional query | FAQ list | marketplace support views | Support service uses canonical `/faqs/` | help screen | YES | YES | YES | YES | COMPLETE | P2 | `/help/faq/` is compatibility alias |
| POST | `/support/help/feedback/` | Feedback | JWT/customer | category/message | created acknowledgment | support view | Support service | feedback form | YES | YES | YES | YES | COMPLETE | P2 | None |
| GET | `/support/home/special-offers/`, `/{id}/` | Offers | Public/customer | optional filters/id | offer details | marketplace offer views | Support service | home offers | YES | YES | YES | YES | COMPLETE | P2 | Detail is reached from offer selection |
| GET | `/support/legal/`, `/support/legal/{type}/` | Legacy legal alias | Public | type | legal text | support views | no direct mobile call | canonical `/legal/*` screens | NO | N/A | N/A | YES | NOT_APPLICABLE | P3 | Retain compatibility only |
| GET, PATCH, POST, DELETE | `/support/notifications/*` list, detail, read, read-all, unread, preferences, push-device, track | Notifications | JWT/customer | device token/preferences/event | notifications, counts, preference state | marketplace notification views/services | Notification service/context | inbox, banner, settings, tap routing | YES | YES | YES | YES | COMPLETE | P1 | Production Expo credential and physical-device delivery remain manual |

## Intentionally Non-Mobile Operations

- Owner-only: all `/laundries/dashboard/*`, owner order lifecycle transitions, `/payments/owner-stats/`, `/logistics/assignments/*`.
- Driver/owner/admin: accept, reject, quote, picked-up, washed, out-for-delivery, delivered and complete lifecycle operations.
- Staff/admin: analytics dashboards, campaign management, refunds, payment analytics, legal administration, user/laundry moderation and admin notifications.
- Infrastructure: Clerk webhook, Paystack callback/webhooks, schema/router roots and format-suffix routes.

These are `OWNER_ONLY_EXPECTED`, `ADMIN_ONLY_EXPECTED`, or infrastructure `NOT_APPLICABLE`, not missing customer features.

## Real Gaps Found

1. **Fixed P0 - payment state loss:** the mobile normalizer discarded `payment_reference`, provider payment status and payment method. It now preserves them and maps `PENDING`, `SUCCESS`, `FAILED`, `EXPIRED`, `REFUND_PENDING`, `REFUNDED`, cash-due and quote-awaiting states into accurate UI actions.
2. **Fixed P0 - unsafe generic order mutation:** DRF previously exposed generic PUT/PATCH/DELETE on orders, bypassing lifecycle rules. The viewset is now create/read-only plus explicit lifecycle actions.
3. **Fixed P0 - account deletion identity gap:** local deletion did not reliably delete the Clerk identity. The backend now fails closed when Clerk deletion cannot be completed, then transactionally anonymizes local identity data and revokes sessions while retaining legally necessary transaction records under a tombstoned user.
4. **Fixed P1 - Paystack ownership check order:** verification now rejects a foreign reference before contacting Paystack.
5. **Frontend-only P2:** `app/settings/payment.tsx` is an orphaned “Payment Methods Placeholder.” There is no backend saved-card/payment-method product. It is not linked from Settings and must not be promoted until product, PCI and tokenization rules exist.
6. **Fixed P1 - approved COD/custom-quote acceptance:** COD is now an explicit order method and custom quote remains a separate pricing mode. Both may be accepted before payment; ordinary unpaid online orders may not. Cash becomes paid only through the audited collection action.

## OpenAPI Drift

- Regeneration: `python manage.py spectacular --file docs/api/simame-openapi.yaml --validate` -> 0 errors, 3 enum-name warnings.
- Actual but intentionally undocumented: Paystack browser callback and two webhook aliases. They should receive explicit schema annotations in a later documentation pass; runtime authentication/idempotency tests exist.
- Removed from the regenerated contract: generic PUT/PATCH/DELETE order operations that are no longer allowed.
- Added to order responses: payment method/state, amount due/collected, cash collection time, and nullable online provider status/reference.

## Approved COD And Custom-Quote Policy

> Simame supports Cash on Delivery. Cash/COD orders may be accepted by an owner before payment and remain financially unpaid until cash is actually collected. Custom-quote orders may also be accepted before payment. Paystack/online-payment controls remain strict and separate.

Implementation parity:

| Capability | Backend authority | Customer mobile | Owner web | Financial effect |
|---|---|---|---|---|
| Select COD | Order payment method is CASH; no Payment row at booking | Checkout sends CASH; never opens Paystack | Shows Cash on Delivery and amount due | None until collection |
| Accept unpaid COD | Explicit lifecycle exception for payment method CASH | Shows accepted order with cash still due | Accept remains available | No settlement, payout or paid earnings |
| Accept custom quote | Explicit lifecycle exception for CUSTOM_QUOTE pricing | Keeps selected payment method while awaiting quote | Accept remains available | No paid earnings until actual payment |
| Collect COD | POST /orders/lifecycle/{id}/collect-cash/; owner/admin, exact amount, fulfillment state, transaction and row lock | Refresh renders Paid in Cash, amount and time | Confirmation dialog with loading/error/refresh | Successful CASH payment; no Paystack settlement |
| Online order | Payment success required before ordinary acceptance | Backend Paystack initialization/verify flow | Accept hidden while unpaid and backend rejects bypass | Existing settlement/payout path only |

Order responses now expose payment method/state, amount due, amount collected, cash collection time, online provider status and online reference. Provider fields are null for COD. Awaiting quote is distinct from the payment method.

The prior product-policy P1 is resolved by approved policy and server-side enforcement. Remaining release blockers are independent and unchanged.
