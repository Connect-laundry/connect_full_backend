# SIMAME Admin Order Notification Certification

Revision 2 · 2026-09-27 · `connect_new_backend`, branch `main` (HEAD `d1e455b`)

Status: **uncommitted, unpushed.**

## FINAL VERDICT

## 🟡 CODE READY — EXTERNAL/OPERATIONAL GATE REMAINS

Every gate that can be closed in code is closed. That covers the financial fixes, production URL enforcement and admin status safety, and each one is tested on SQLite and real PostgreSQL.

The provider, staging and canary gates are **not passed**:

- no Arkesel SMS or WhatsApp credentials exist in any environment reachable from here;
- the WhatsApp templates have not been submitted to Meta;
- the staging service is suspended;
- no real message has been sent.

GREEN cannot be claimed until "Manual actions" 1–10 below are done.

---

## GIT

| | |
|---|---|
| Local work preserved | Yes. No reset, stash, checkout or force operation was run. Nothing is staged. No local commits were made; this pass needed none for rollback, since every change is uncommitted and inspectable with `git diff` |
| Unrelated work | None in the backend working tree. `connect-customer-mobile` (HEAD `d4947b8`) and `Connect-Web-App` (HEAD `6e208cc`) have **clean working trees** and were not modified |
| Ignored files not to commit | `.env`, `*.log` (six launch logs), `test_db.sqlite3`, `__pycache__/`. All are already git-ignored |
| `git diff --check` | clean. New files: no trailing whitespace, all LF, final newline present |
| Secret scan | Changed and new files searched for live-key/JWT/private-key/token patterns. The only hits are deliberate fake values in tests (`sk_live_paystack_secret`, `ark-test-key-DO-NOT-LEAK`) that assert secrets never reach messages |

### Feature files

**Modified (12)**

| File | Change |
|---|---|
| `.env.example` | variable names only, no values |
| `config/settings.py` | env-driven settings, all OFF by default |
| `config/test_settings.py` | |
| `config/urls.py`, `config/test_urls.py` | callback route |
| `ordering/serializers/order.py` | NEW_ORDER emit; coupon redemption for every pricing mode |
| `ordering/views/lifecycle.py` | uses the shared transition service; cash-collected and quote emits |
| `ordering/admin.py` | locked raw fields; Cancel/Reject actions; quote emit |
| `payments/admin.py` | reconcile fix |
| `payments/views.py`, `payments/webhooks.py`, `payments/tasks.py` | one-line PAYMENT_CONFIRMED emits |

**New**

| File | Purpose |
|---|---|
| `admin_notifications/` | app, migration `0001_initial`, tests |
| `payments/services/verified_payment.py` | shared "apply verified Paystack success" |
| `ordering/services/lifecycle_transitions.py` | shared actor-driven status change |
| `tests/test_admin_reconcile_settlement.py` | reconcile regression |
| `ordering/tests/test_coupon_accounting_all_modes.py` | coupon regression |
| `ordering/tests/test_cancel_after_pickup_no_refund.py` | refund regression |
| `ordering/tests/test_admin_order_status_guard.py` | admin status regression |
| this report | |

---

## FINANCIAL

### 1. Manual payment reconciliation — FIXED

**Defect:** Django admin *Payments → Force Reconcile via Paystack* set the Payment to SUCCESS and the Order to PAID + CONFIRMED but **never called `SettlementService.record_for_order`**. `release_for_order` returns early when no settlement exists, so the laundry was never owed the money. The action also locked Payment before Order (the reverse of the platform-wide order, so a deadlock risk against webhooks), held the locks during the Paystack HTTP call, and skipped the audit record and the Paystack-channel payment method.

**Fix:**

- New `payments/services/verified_payment.apply_verified_paystack_success` mirrors the customer verify endpoint step for step:
  - validation → Payment SUCCESS + sanitized response + `paid_at` + channel method;
  - Order PAID;
  - `SettlementService.record_for_order(order, processor_fee=_paystack_fee(...), settled_directly=...)`, the single Model A settlement calculation;
  - `OrderStateMachine` → CONFIRMED;
  - PAYMENT_CONFIRMED alert;
  - audit `PAYMENT_ADMIN_RECONCILED`.
- It locks Order then Payment.
- The admin action now calls Paystack **before** locking anything, then applies the result through this function.
- No new money logic was written.

**Evidence** (`tests/test_admin_reconcile_settlement.py`, 5 tests):

- Two identical orders, with in-app transport and a laundry-funded free-transport promo:
  - one is confirmed through the customer verify endpoint, the other through the admin action;
  - `gross_amount`, `platform_commission`, `processor_fee`, `logistics_subsidy_deducted`, `net_payable`, `currency`, `route` and `status` are identical;
  - payment status/method/`paid_at` and order status/payment status are identical;
  - net payable = total − transport (rider's) − commission − laundry promo;
  - settlement status HELD, so payout eligibility is unchanged (released only after delivery, as normal).
- Re-running the action calls Paystack once and creates one settlement.
- Applying twice returns `already_done`, with no double credit.
- An amount mismatch leads to FAILED with no settlement.
- Abandoned leads to FAILED with no settlement.
- **Against the original `payments/admin.py` the two key tests fail**, which proves they catch the bug.

### 2. Coupon accounting — FIXED (wider than reported)

**Defect:** only itemised bookings reached the locked coupon redemption. **By-weight *and* pay-after-quote** bookings attached the coupon and got the discount, but never created `CouponUsage` or incremented `current_usage`. So `max_usage` and per-customer limits could be bypassed through them.

**Fix:** the existing locked redemption block (`select_for_update` on the coupon, limit re-checks under the lock, `CouponUsage` + `F()` increment) moved unchanged into `_redeem_coupon()`. It is now called by all three pricing modes inside the booking transaction, and also re-checks active/expiry under the lock. Per-order uniqueness is enforced by the existing `CouponUsage.order` one-to-one constraint.

**Evidence** (`ordering/tests/test_coupon_accounting_all_modes.py`, 13 tests):

| Case | Result |
|---|---|
| First use counted | BY_ITEM, BY_WEIGHT, CUSTOM_QUOTE |
| Second use by the same customer | refused (BY_ITEM, BY_WEIGHT) |
| `user_limit=2` | holds across modes |
| `max_usage` | cannot be exceeded with weight orders |
| One usage per order | DB-enforced |
| Expired and disabled coupons | refused, no usage written, no order created |
| **PostgreSQL: 12 concurrent weight bookings on a `max_usage=3` coupon** | exactly 3 succeed, `current_usage=3`, 3 usages |
| Against the original serializer | 6 tests fail |

### 3. Unsafe admin status transitions — CLOSED

**Defect:** `OrderAdmin` exposed `status`, `total_amount`, `user` and `laundry` as raw editable fields. Staff could write CANCELLED/REJECTED/DELIVERED/COMPLETED straight to the row, bypassing:

- refunds;
- settlement release and dispute-window rules;
- status history;
- customer notifications;
- operations alerts.

They could also move an order to another laundry or change its total.

**Fix:**

- Those four fields are read-only on existing orders.
- New admin actions **Cancel selected orders (refunds paid online orders)** and **Reject selected orders** run through `ordering/services/lifecycle_transitions.perform_transition`, the **same function the lifecycle API now uses**. There is no second state machine; legality still comes from `OrderStateMachine`.
- Both actions need the `change` permission. Delivered/completed are intentionally not offered to staff, because they depend on the laundry and handover-code/dispute-window flow.

### 4. NEW P0-class defect found and fixed: refund issued before transition check

While extracting the lifecycle service, I proved on the original code that a **customer "cancel" on a paid order already PICKED_UP, IN_PROCESS, OUT_FOR_DELIVERY, DELIVERED or COMPLETED called `refund_payment` first**. The API then answered 400 "Invalid state transition", but the refund had been started and committed. The laundry did the work and the customer got the money back. An owner "reject" in those states did the same.

**Fix:** `perform_transition` checks `OrderStateMachine.can_transition` **before** any money moves. The existing COD "confirm cash first" (409) and "payment must be confirmed" (409) gates keep their original precedence, so API responses are unchanged. A REFUND_PENDING claim from an ambiguous Paystack refund is still committed, not rolled back, exactly as before.

**Evidence** (`ordering/tests/test_cancel_after_pickup_no_refund.py`):

- 10 illegal cancel/reject cases: no refund, no provider call, order unchanged.
- A legal cancel still refunds.
- On the original code 7+ cases fail.
- All existing lifecycle, payment, refund, dispute, handover, COD, settlement and certification tests pass (329 in the targeted run).

Admin evidence (`ordering/tests/test_admin_order_status_guard.py`, 9 tests):

- raw POSTs of CANCELLED/REJECTED/COMPLETED/DELIVERED plus total and laundry save with 302 and are **ignored**, with no history and no alert;
- the change page renders no editable status/total/laundry input;
- admin cancel of a paid CONFIRMED order goes through the lifecycle refund, one history row, one ORDER_CANCELLED alert;
- admin cancel of a DELIVERED order is refused with no refund;
- admin reject alerts ops;
- view-only staff cannot run the actions.

---

## PRODUCTION ADMIN URL — FIXED

- In production, `ADMIN_NOTIFICATION_ADMIN_BASE_URL` is **mandatory**; there is no fallback to `ADMIN_BASE_URL`, whose non-DEBUG default is the suspended staging host.
- It must be an `https://` origin (no path), must not be `connect-full-backend.onrender.com` (configurable deny-list `ADMIN_NOTIFICATION_FORBIDDEN_HOSTS`), and must not be localhost/`.test`/`.local`/`.example`. Otherwise `check_admin_notifications` reports `[ERROR]` and exits 1.
- If it is still misconfigured at runtime, alerts say "Admin link unavailable (ADMIN_NOTIFICATION_ADMIN_BASE_URL not configured)" instead of linking to the wrong server.
- `ARKESEL_SMS_CALLBACK_BASE_URL` follows the same rule.
- Tests (`admin_notifications/tests/test_production_urls.py`, 10):
  - 6 bad-origin cases fail preflight;
  - staging is never used even when `ADMIN_BASE_URL` points at it;
  - the production link is exactly `https://connect-full-backend-production.onrender.com/admin/ordering/order/<id>/change/`;
  - **anonymous access to that path redirects to `/admin/login/`**.

---

## PROVIDER

| Gate | Status |
|---|---|
| Arkesel SMS API verified | **Contract verified** against Arkesel's official OpenAPI spec (`developers.arkesel.com/spec/api_spec.v2.4.0.yaml`): `POST https://sms.arkesel.com/api/v2/sms/send`, header `api-key`, body `sender/message/recipients[]/callback_url/sandbox`, statuses 200/401/402/403/422/500, callback query `sms_id` + `status`. 30 provider-contract tests. **Not verified against the live API** (no key) |
| SMS segmentation | Spec: "A one-page message = 160 characters. A 200 characters message will be 2 pages." Arkesel concatenates natively; no maximum is documented, so the app keeps full detail and splits only above 918 chars (6 GSM segments) into labelled parts. Splits happen **only at line boundaries**. A line is wrapped only if it is longer than a whole part, and then never between "GHS" and its amount. Tested with 12-, 24- and 36-line orders: every item line appears whole in exactly one part |
| SMS sender approved | **NOT VERIFIED** (Arkesel account access needed) |
| SMS real delivery | **NOT DONE** |
| SMS callback | Implemented and tested: all 6 statuses, duplicates, replay, unknown id, invalid status, malformed/oversized/SQL-ish/NUL input, and cross-order isolation (the callback for order A leaves order B untouched). **Real callback NOT observed** |
| WhatsApp API verified | Contract verified from the same official spec (KOVA IQ `send-template-message`, `X-API-Token`, org pid, inbox afr_uuid, template afr_uuid). Official WhatsApp Business Platform only; no web automation of any kind. **Account access NOT verified** |
| WhatsApp template approved | **PENDING — not yet submitted.** Exact bodies are in the Appendix |
| 3 WhatsApp recipients verified | **NOT DONE** (fan-out to 233551057139 / 233200031713 / 233541604786 proven with mocks only) |

---

## SECURITY

| | |
|---|---|
| Secret scan | Clean (see GIT). Secrets are read only in `admin_notifications/conf.py` and never printed; preflight shows `sha256:` fingerprints |
| PII/log scan | Logs and Sentry carry only event, channel, provider, event type, environment, error class and order number. Tracebacks go to server logs only, because Sentry frame locals would carry addresses. Provider error text is capped and digit runs are masked |
| Handover code | Test sets code `4821` and asserts it is absent from every payload and rendered message, together with the Paystack secret, Arkesel key, WhatsApp token, `authorization_code`, JWT prefix and "password" |
| Callback security | Secret path compared in constant time; wrong or unset secret gives 404. Six-value status allowlist. **`sms_id` must match a strict id charset**: a NUL byte previously caused a PostgreSQL `DataError` (500). This was found by the PG run, fixed and re-tested. The id must be ours, updates are row-locked, final statuses are never downgraded, and every response is `{"ok": true}`. Residual risk: the secret appears in Render access logs |
| Cross-order isolation | PostgreSQL: 20 simultaneous bookings, each payload matching its own order/customer/laundry/address/items/total. Callback cross-order test |

---

## FUNCTIONAL

| Case | Result (tested) |
|---|---|
| COD | NEW_ORDER at booking commit: **Payment method: CASH ON DELIVERY · Payment status: NOT YET PAID · Amount to collect: GHS X**; plus Paid / Outstanding |
| Paystack | NEW_ORDER **Payment method: PAYSTACK · Payment status: PENDING (not yet confirmed by Paystack)** + "Wait for PAYMENT CONFIRMED before dispatching a rider". PAYMENT_CONFIRMED only after server verification: webhook, verify endpoint, reconcile task or admin reconcile. Never a second NEW_ORDER |
| Cash collection | PAYMENT_CONFIRMED ("CASH COLLECTED") only from the real `collect-cash` workflow; repeat calls → 1 event |
| PER_ITEM | `2 x Shirt (Wash & Iron) @ GHS 10.00 = GHS 20.00`. The service variant was added this pass, frozen at booking |
| PER_KG | **Rate · Estimated weight · Estimated charge · Final charge: NOT CONFIRMED (set after the laundry weighs) · ESTIMATED TOTAL**; COD "Amount to collect: about GHS X (ESTIMATE…)". A test asserts no un-prefixed "TOTAL: GHS" appears |
| Transport | "Transport charged: GHS X (pickup A, delivery B)" or "Transport: not billed in the app", plus "Rider cost: GHS Y" when known. Frozen at booking |
| Long orders | SMS and WhatsApp split at item boundaries only (12/24/36 lines). WhatsApp parts ≤ 1024 chars with legal parameters |
| Cancellation / rejection | "DO NOT DISPATCH A RIDER", with reason, previous status and original pickup. Duplicate API calls → 1 alert; **10 concurrent cancels on PostgreSQL → 1 alert, 1 history row** |
| Future order-change events | No-change / whitespace-only / same-number saves → no alert; a real change → one update alert with old → new. No endpoint edits these fields today |
| Trigger | NEW_ORDER is recorded inside the booking transaction after the order, lines, frozen price and coupon usage exist and the last stale-quote check has passed. A rejected booking rolls the alert back. Checkout, the Paystack popup and mobile success screens create nothing |
| Landmark | The booking API has no separate landmark field. Landmarks typed into the address are kept verbatim. Delivery date is not collected by the booking API ("Not scheduled yet (arrange with laundry)") |

## CONCURRENCY (PostgreSQL 17.10, real migrations, throwaway instance)

**212 passed** across all admin_notifications tests, the new financial regressions and the existing Model A `tests/test_postgres_concurrency.py`. Among them:

| Case | Result |
|---|---|
| 20 same NEW_ORDER emits | 1 event, 3 WhatsApp + 1 SMS rows |
| 20 different simultaneous orders | no data mixing |
| 5 dispatch workers | 80 sends, 80 unique, 0 duplicates |
| 10 duplicate Paystack webhooks | 1 PAYMENT_CONFIRMED |
| duplicate SMS callbacks | harmless |
| 10 concurrent cancels | 1 alert |
| 12 concurrent coupon redemptions (limit 3) | exactly 3 |
| Model A payout/settlement races | green |

## PERFORMANCE

- Measured on PostgreSQL with 0.3 s simulated latency per provider call.
- **Booking response 0.21–0.31 s; all 4 alerts accepted 1.54–1.74 s after the booking started.** The higher figures were measured while the full suite ran in parallel.
- Provider calls never run inside the booking transaction.
- One background worker per process, and kicks are coalesced, so thread count is bounded.
- Retries are bounded: 6 attempts, 30 s → 2 h backoff, 12 h expiry.

## TESTS

| Suite | Result |
|---|---|
| Backend full (SQLite) | **1400 passed, 22 skipped, 0 failed**. First pass: 1342/19. Pre-feature baseline: 1207/13. The skips are PostgreSQL-only tests, run separately above |
| PostgreSQL | **212 passed** |
| Model A regression | green in both runs (payments, settlements, payouts, refunds, handover, customer confirmation, disputes, cross-laundry, concurrency) |
| Mobile | **MOBILE CODE CHANGED: NO.** `connect-customer-mobile` jest: **64 suites, 498 tests passed**; tree clean |
| Migrations | No new migration this pass (`makemigrations --check`: no changes). `admin_notifications.0001` previously verified on PostgreSQL: fresh install, rollback and re-apply over existing orders |

## FAILURE DRILLS (mocked providers)

| Scenario | Order | Notification | Outbox | Ops can see order |
|---|---|---|---|---|
| Arkesel down / DNS / connect timeout | 201 | RETRY → DEAD after 6 | persisted | yes |
| WhatsApp down | 201 | RETRY | persisted | yes |
| Both down | 201 | RETRY | persisted | yes |
| Invalid Arkesel key (401) / invalid WhatsApp token | 201 | FAILED `AUTH_FAILED`, no retry loop | persisted | yes |
| Insufficient SMS balance (402) | 201 | FAILED `INSUFFICIENT_BALANCE`, fatal Sentry, one attempt; WhatsApp still sent | persisted | yes |
| Invalid sender (422) / invalid template (422) | 201 | FAILED `INVALID_SENDER` / `TEMPLATE_INVALID` | persisted | yes |
| Response lost after send (read timeout) | 201 | **UNKNOWN, not auto-resent** (possible-duplicate risk surfaced; manual retry in admin) | persisted | yes |
| Snapshot bug / outbox table missing mid-deploy | 201 | not recorded, alerted | n/a | yes |

**Ambiguous-send policy (final):** Arkesel returns no id when the response is lost, and its report endpoint needs that id. So a send whose outcome is unknown is **not** retried automatically. It is marked UNKNOWN with a Sentry warning and can be re-queued from Django admin. A failure before the connection is established is always safe to retry.

## STAGING — NOT RUN

`connect-full-backend.onrender.com` is suspended and no staging Arkesel credentials or QA recipients exist.

## PRODUCTION CANARY — NOT RUN

- COD canary: **NOT RUN**
- Paystack canary: **NOT RUN**

## ROLLBACK

- `ADMIN_ORDER_NOTIFICATIONS_ENABLED=false` stops recording and sending (tested: booking still 201, rows stay PENDING).
- Channels switch off independently.
- The financial fixes do not depend on the notification flags.
- Migration rollback: `migrate admin_notifications zero` drops only the three new tables.

---

## REMAINING BLOCKERS

1. Arkesel SMS API key; SIMAME sender approval; SMS balance.
2. Arkesel KOVA IQ WhatsApp access: API token, organization pid, WhatsApp inbox afr_uuid.
3. Submit **both** templates (UTILITY/en) and get them approved.
4. Written consent from the three admin numbers to receive WhatsApp alerts (Meta opt-in).
5. Staging run (service suspended). Production preflight with real values. COD and Paystack canaries.

## MANUAL ACTIONS FOR PHILIP

1. **Arkesel SMS:** confirm the SIMAME sender ID is approved and the balance is funded, then get the v2 API key.
2. **Arkesel KOVA IQ:** confirm WhatsApp Business API access, create an API token, and note the organization `pid` and the inbox `afr_uuid`.
3. **Submit the two templates** in the Appendix and wait for APPROVED. Note each `afr_uuid`.
4. Generate a callback secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
5. **Commit on a feature branch, open a PR and deploy** (not pushed in this pass). Deploy with every notification flag OFF, which is the default; the migration applies and the financial fixes go live.
6. **Staging** (if revived): `ADMIN_NOTIFICATION_ENVIRONMENT=staging`, `ARKESEL_SMS_SANDBOX=true`, QA recipients only (**not** the production admin numbers), an explicit staging `ADMIN_NOTIFICATION_ADMIN_BASE_URL`. Run `check_admin_notifications --probe`, then one COD, one Paystack, one per-kg, one long order, one cancellation and one payment confirmation. Check the outbox, SMS/WhatsApp, callbacks and links.
7. **Production env** (Render dashboard, `connect-full-backend-production`):
   ```
   ADMIN_NOTIFICATION_ENVIRONMENT=production
   ADMIN_NOTIFICATION_ADMIN_BASE_URL=https://connect-full-backend-production.onrender.com
   ADMIN_WHATSAPP_RECIPIENTS=233551057139,233200031713,233541604786
   ADMIN_SMS_RECIPIENTS=233551057139
   ADMIN_SMS_DETAIL_MODE=full
   ARKESEL_API_KEY=<secret>
   ARKESEL_SENDER_ID=SIMAME
   ARKESEL_SMS_SANDBOX=false
   ARKESEL_SMS_CALLBACK_BASE_URL=https://connect-full-backend-production.onrender.com
   ARKESEL_SMS_CALLBACK_SECRET=<generated>
   ARKESEL_WHATSAPP_API_TOKEN=<secret>
   ARKESEL_WHATSAPP_ORGANIZATION_ID=<pid>
   ARKESEL_WHATSAPP_INBOX_ID=<inbox afr_uuid>
   ARKESEL_WHATSAPP_NEW_ORDER_TEMPLATE_ID=<approved afr_uuid>
   ARKESEL_WHATSAPP_ORDER_UPDATE_TEMPLATE_ID=<approved afr_uuid>
   ```
   Note that the project's variable is `ADMIN_SMS_RECIPIENTS`, not `ARKESEL_SMS_RECIPIENTS`.
8. **Preflight:** `python manage.py check_admin_notifications --probe`. It sends nothing. It checks the environment, HTTPS, sender, recipients, credential presence (fingerprints only), template IDs *and their APPROVED status on the account*, the callback URL, the admin URL, sandbox mode and migrations. Continue only with zero `[ERROR]` lines.
9. **COD canary:**
   1. Set `ADMIN_ORDER_NOTIFICATIONS_ENABLED=true` and both channel flags `=true`.
   2. Place one real QA COD order.
   3. Confirm all three WhatsApp numbers show the message on the handset. HTTP success is not enough.
   4. Confirm the SMS reaches 0551057139, and the SMS delivery row turns DELIVERED after the Arkesel callback (reports arrive in batches, about every 10 minutes).
   5. Compare every value with Django admin/PostgreSQL. Open the admin link, which must require login.
10. **Paystack canary:** one controlled order. NEW_ORDER shows PENDING. After payment, exactly one PAYMENT_CONFIRMED. The settlement exists in admin.
11. Remove any QA recipients from production. Keep `ARKESEL_SMS_SANDBOX=false`. Run `admin_notification_report --days 1`.

---

## APPENDIX — WhatsApp templates (category UTILITY, language en)

`simame_admin_new_order_v1`
```
SIMAME NEW ORDER {{1}}
Booked: {{2}}

Customer: {{3}}
Laundry: {{4}}
Payment: {{5}}

Pickup: {{6}}
Delivery: {{7}}

Items: {{8}}
Amounts: {{9}}
Notes: {{10}}

Admin (login required): {{11}}
Please coordinate the rider for this order.
```
Sample: `CN-62597379` · `Sun 27 Sep 2026, 3:28 PM GMT` · `Ama Mensah, 055 478 3497` · `Adepa Laundry, 024 111 2233, 12 Ring Road, Adum, Kumasi` · `CASH ON DELIVERY - NOT YET PAID - Amount to collect: GHS 72.80` · `Mon 28 Sep 2026, 5:00 PM - 6:00 PM GMT | KNUST Ayeduase | Map: https://www.google.com/maps/search/?api=1&query=5.604,-0.186` · `Not scheduled yet (arrange with laundry) | Same address as pickup` · `2 x Shirt (Wash & Iron) @ GHS 10.00 = GHS 20.00` · `Subtotal: GHS 65.00 | Transport: not billed in the app | TOTAL: GHS 72.80 | Paid: GHS 0.00 | Outstanding: GHS 72.80` · `Gate is blue` · `https://connect-full-backend-production.onrender.com/admin/ordering/order/<id>/change/`

`simame_admin_order_update_v1`
```
SIMAME ORDER UPDATE
Update: {{1}}
Order: {{2}}
Details: {{3}}
Admin (login required): {{4}}
This is an automated Simame operations alert.
```
Sample: `PAYMENT CONFIRMED` · `CN-62597379` · `Amount: GHS 72.80 | Method: Mobile Money via Paystack | Reference: ORD-… | Paid at: …` · `https://…/admin/ordering/order/<id>/change/`

## OPERATIONS REFERENCE

- `python manage.py check_admin_notifications [--probe]`
- `python manage.py dispatch_admin_notifications [--limit N] [--event-id UUID] [--channel sms|whatsapp] [--dry-run] [--retry-failed]`
- `python manage.py admin_notification_report [--days N]`: orders notified, COD vs online, per-channel success %, retries, dead, unknown, SMS submitted vs delivered, segments, p50/p90 latency.
- Django admin → Admin notification events/deliveries: statuses PENDING / SENDING / RETRY / SUBMITTED / SENT / DELIVERED / UNDELIVERED / UNKNOWN / FAILED / DEAD, masked recipients, provider ids, per-part SMS status, rendered preview, Retry action.
- Django admin → Orders: **Cancel** / **Reject** actions (lifecycle, refunds, alerts); status, total, customer and laundry are read-only.
