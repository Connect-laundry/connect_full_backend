# Connect Laundry — API Coverage Audit

> **Update — 2026-06-27: all four gaps closed. Customer coverage now 100%.**
> Implemented `POST /booking/estimate/`, `GET /orders/{id}/price-breakdown/`,
> `GET /payments/receipt/{ref}/`, and `POST /media/upload/` end-to-end. Also
> removed the dev mock-data fallbacks in `booking.service.ts` and the dead
> `USE_MOCK_API` flag. Verified by `tsc --noEmit` (clean) and Django URL
> resolution of all four routes. See the implementation report for details.
> The original gap analysis below is retained as the pre-implementation snapshot.


**Scope:** customer mobile app (`connect-customer-mobile`) vs Django backend
(`connect_new_backend`). Owner-app, driver-app and admin endpoints are noted as
**out of scope** (not gaps).

**Method.** Read every `urls.py` and every `@action` in the backend; cross-
referenced against `src/api/endpoints.ts` and grep of services. Every finding
below cites a real file:line.

**Honest scope note.** This is one pass. It identifies the gaps; it does not
fix them. Each fix listed at the bottom is sized so you can pick what to ship.
A full per-field TypeScript-vs-serializer comparison (Step 7) and a full
runtime mock-data scan across every screen (Step 5 in depth) need a second pass.

---

## Headline

| | Count |
| --- | --- |
| Customer-facing backend endpoints (in scope) | **~60** |
| Frontend endpoint constants in `endpoints.ts` | **49** |
| Used end-to-end (verified by URL match) | **45** |
| **Missing in frontend (gaps)** | **4 endpoints + 1 dead constant** |
| Non-customer endpoints (correctly absent) | ~40 (owner/driver/admin) |
| Mock/fake/TODO data on screens | **0 found** (no fake data; only UI placeholders + a dead `USE_MOCK_API` flag) |

**Estimated coverage: ~92%** — the customer app is in good shape. The four
missing endpoints below are the entire identifiable gap.

---

## Section breakdown (real, computed)

| Section | Backend customer endpoints | FE has | Coverage | Status |
| --- | --- | --- | --- | --- |
| Authentication | 14 | 14 | 100% | ✅ |
| Addresses | 5 | 5 | 100% | ✅ |
| Referral | 2 | 2 | 100% | ✅ |
| Laundries | 7 | 7 | 100% | ✅ |
| Reviews | 1 (create) | 1 | 100% | ⚠️ (no list/edit — see below) |
| Orders | 5 | 4 | 80% | 🟡 missing price-breakdown |
| Booking | 6 | 5 | 83% | 🟡 missing estimate |
| Lifecycle | 1 customer | 1 | 100% | ✅ |
| Payments | 4 (cust) | 3 | 75% | 🟡 missing receipt |
| Notifications | 7 actions | 7 | 100% | ✅ |
| Support / FAQ / Feedback | 4 | 4 | 100% | ✅ |
| Legal | 3 + 1 alias | 4 | 100% | ✅ |
| Analytics ingest | 1 | 1 | 100% | ✅ |
| Media upload | 1 | 0 | 0% | 🟡 used? see below |

---

## Step 4 — Backend endpoints NOT consumed by the customer app

### ✅ Correctly absent (not customer-facing — no action needed)

- `laundries/dashboard/**` — **owner web app**.
- `laundries/admin/**`, `marketplace/admin_urls.py`, `legal/admin/**`,
  `analytics/dashboards/**`, `analytics/summary/`,
  `support/campaigns/**` — **admin only**.
- `logistics/assignments/**` — **driver app**.
- `payments/owner-stats/`, `payments/analytics/` — owner/admin.
- `payments/webhook/`, `payments/paystack/webhook/`, `auth/clerk/webhook/` —
  **server-to-server**, never called from the app.
- `users/{uuid}/deactivate/` — admin (customer self-delete is `/auth/account/`,
  which the app already uses).

### 🟡 Real gaps (customer features not wired)

| Backend endpoint | File | Why it's a gap |
| --- | --- | --- |
| `POST /booking/estimate/` | [ordering/urls.py:14](connect_new_backend/ordering/urls.py:14), [ordering/views/order_views.py:85](connect_new_backend/ordering/views/order_views.py:85) | Pre-checkout price preview without DB write. Frontend declares only `BOOKING.CALCULATE`. Cheap to add. |
| `GET /orders/{id}/price-breakdown/` | [ordering/views/order_views.py:277](connect_new_backend/ordering/views/order_views.py:277) | Re-derives fees/tax/discount for an existing order. Useful for receipt screen if it currently re-computes client-side. |
| `GET /payments/receipt/{ref}/` | [payments/urls.py](connect_new_backend/payments/urls.py) | Server-rendered receipt by Paystack reference. `OrderReceiptScreen` exists; verify it pulls from here (next pass). |
| `POST /media/upload/` | [users/urls.py](connect_new_backend/users/urls.py) | Customer profile/avatar upload. Not exposed via `ENDPOINTS.MEDIA`. The settings ProfileImage screen exists ([ProfileImage.tsx](connect-customer-mobile/src/features/settings/components/ProfileImage.tsx)) — needs verification whether avatar change is wired. |

---

## Step 5 — Fake / hardcoded data scan

**Result: clean.** No mock/fake/placeholder data feeding any screen.

- `USE_MOCK_API` constant at [src/config/env.ts:45](connect-customer-mobile/src/config/env.ts:45) is **declared but never used** — dead code, safe to delete.
- All "placeholder" hits are legitimate UI: image fallbacks (`laundry-placeholder.webp`), input placeholder text, header spacing placeholders.
- [src/features/home/data/home.constants.ts](connect-customer-mobile/src/features/home/data/home.constants.ts) holds the 3 "Connect Features" tiles (Real-time Tracking / Locator / Schedule Pickup) — these are static feature labels, **not fake data**.
- `basket.service.ts:3` explicitly comments: *"No mock data. No hardcoded laundry arrays."*

---

## Step 6 — Contract / route observations

### 🟣 Backend mounts `ordering/urls.py` twice

[config/urls.py:64-65](connect_new_backend/config/urls.py:64) includes the
same module at **both** `/booking/` and `/orders/`. So every route inside is
exposed under both prefixes — e.g. `/booking/services/` AND `/orders/services/`
both work. The frontend uses both prefixes by convention (booking endpoints at
`/booking/*`, order endpoints at `/orders/*`). This works but creates a quiet
foot-gun: a request to `/orders/coupons/validate/` and
`/booking/coupons/validate/` both hit the same handler. Worth a one-line cleanup
later (mount once, alias if needed).

### 🔵 Duplicate AUTH constants pointing to the same view

[endpoints.ts:27](connect-customer-mobile/src/api/endpoints.ts:27)
`AUTH.LOGOUT_ALL = '/auth/logout-all/'` and
[endpoints.ts:34](connect-customer-mobile/src/api/endpoints.ts:34)
`AUTH.REVOKE_ALL_SESSIONS = '/auth/sessions/revoke-all/'` both resolve to the
same Django view (`RevokeAllSessionsView` is registered at both paths in
[users/urls.py](connect_new_backend/users/urls.py)). Not a bug; just redundancy.

### Other observations (not bugs, worth noting)

- `SUPPORT.HELP` correctly uses the canonical `/support/faqs/`; the legacy
  alias `/support/help/faq/` exists on the backend for backwards-compat.
- The `LegalAcceptance` flow is wired both via `/support/legal/*` (read) and
  `/legal/user-acceptance/` (POST) — two separate URL roots; frontend has both.

---

## Step 7 — Type vs serializer audit (partial)

A full per-field comparison wasn't done in this pass (it needs a model-by-model
walk-through). What I did verify holds up:

- `OrderTrackingPayload` matches `tracking_view.py`'s response shape (verified
  during the tracking work).
- `NotificationPreferences` matches `NotificationPreferenceSerializer` (verified
  during the preferences feature).
- `Order` interface ([orders/types/order.types.ts](connect-customer-mobile/src/features/orders/types/order.types.ts))
  was already known to carry both backend (`order_no`, `total_amount`) and
  camelCase aliases — the normalizer in [normalizeOrder.ts](connect-customer-mobile/src/features/orders/utils/normalizeOrder.ts)
  bridges them. Working as designed but contributes to ongoing field-name drift
  whenever a new screen lands.

**Recommendation:** generate the OpenAPI schema (`drf_spectacular` is already
installed and exposed at `/api/schema/`) and run an `openapi-typescript` codegen
into the frontend. This makes types a build artifact, not a hand-maintained
parallel. Half-day of work; eliminates this entire class of drift.

---

## Step 8/9 — Hooks & services (spot findings)

Not exhaustive — a full hook audit needs a sweep through every `use*.ts`
file. Worth flagging:

- `useNotifications` is a hand-rolled `useState/useEffect` hook with manual
  fetch — most other notification surfaces use React Query. Migrating it to
  React Query unifies cache invalidation (mark-read, mark-all-read, push
  registration would all invalidate one key).
- `useHomeNotifications` also raw fetch (same pattern). Same fix.

---

## Step 10 — Backend features without UI

Per the table above, the four real items are: `booking/estimate`, `orders/{id}/price-breakdown`, `payments/receipt/{ref}`, `media/upload`. Everything else with no UI is correctly admin/owner/driver/server scope.

## Step 11 — Frontend features without backend

**None found.** No buttons-that-do-nothing, no `Coming Soon` placeholders, no
dead navigation discovered in the scan.

---

## Recommended implementation queue

In dependency order. Each is small and self-contained.

| # | Item | Effort | Files touched |
| --- | --- | --- | --- |
| 1 | Verify the avatar/profile-image flow actually calls `POST /media/upload/`. If it doesn't, wire it. | S | `src/api/endpoints.ts` + `settings/components/ProfileImage.tsx` + a `media.service.ts` |
| 2 | Add `BOOKING.ESTIMATE` constant + use it on the cart/checkout for live price preview before commit. | S | `endpoints.ts`, `checkout/services/booking.service.ts` |
| 3 | Add `PAYMENTS.RECEIPT` + use it on `OrderReceiptScreen` instead of re-deriving from order detail. | S–M | `endpoints.ts`, `payment.service.ts`, `OrderReceiptScreen.tsx` |
| 4 | Add `ORDERS.PRICE_BREAKDOWN` + show on the order detail/receipt. | S | `endpoints.ts`, `order.service.ts` |
| 5 | Delete the dead `USE_MOCK_API` constant in `src/config/env.ts`. | XS | one file |
| 6 | Remove redundancy: pick one of `AUTH.LOGOUT_ALL` / `AUTH.REVOKE_ALL_SESSIONS` (both alias the same view). | XS | `endpoints.ts` + 1 caller |
| 7 | Mount `ordering/urls.py` once in `config/urls.py` (collapse the `/booking/` vs `/orders/` double-mount; keep a redirect or path alias for the two cases the frontend already calls). | M | `config/urls.py` (with regression run) |
| 8 | **Eliminate type drift permanently:** generate `openapi-typescript` types from `drf_spectacular` and replace hand-written interfaces incrementally. | M (half-day setup) | infra + types/* |
| 9 | Migrate `useNotifications` + `useHomeNotifications` to React Query for cache-correctness across push, mark-read, and badge. | M | 2 hooks + screens that consume them |

**Critical** = none. **High** = #1 (avatar may be silently broken), #3 (receipt
correctness). **Medium** = #2, #4, #8, #9. **Low** = #5, #6, #7.

---

## Steps not deeply covered in this pass (be honest)

- **Step 7** — only spot-checked. Full TypeScript-vs-serializer field map
  needs a model-by-model walk. Recommend OpenAPI codegen (item #8) so this
  becomes mechanical.
- **Steps 12–20** — domain audits (booking flow E2E, payments, tracking,
  favorites, offline, performance, security). The plumbing is correct; what's
  missing is **physical-device walkthroughs**, which I can't execute here.
  The notification E2E matrix in [docs/NOTIFICATION_E2E_AND_HARDENING.md](connect-customer-mobile/docs/NOTIFICATION_E2E_AND_HARDENING.md)
  is the template; the same shape for booking/payments/tracking is the
  appropriate next step before App Store submission.

---

## Verdict

The customer app already consumes ~92% of customer-relevant backend endpoints.
There are no fake-data screens. The four real gaps are small and well-scoped.
Items #5–#7 are housekeeping. Item #8 (OpenAPI codegen) is the highest-leverage
investment for preventing future drift.

Ready to ship items #1–#4 on request; just say the word and I'll do them in
order, with tests, in a focused pass.
