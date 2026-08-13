# Simame Paystack Production Audit

Date accessed for official source research: 2026-08-13.

## Verdict

Local payment logic is materially hardened and covered by automated tests, but live Paystack production certification is still NO-GO until dashboard configuration, a real low-value live payment, signed webhook delivery, callback return, reconciliation, refund, and ledger evidence are captured in the deployed production environment.

## Official Sources and Decisions

| Source | Relevant guidance | Decision implemented |
| --- | --- | --- |
| https://paystack.com/docs/api/authentication/ | Test and live keys are separate; secret keys must remain backend-only. | Production deploy checks reject test-key prefixes. Mobile contains no Paystack secret. |
| https://paystack.com/docs/api/transaction/ | Initialize from the backend; amount is in currency subunits; reference is unique; verify by reference. | Server computes amount, converts with Decimal, creates the reference, and validates the verified response. |
| https://paystack.com/docs/payments/accept-payments/ | Backend verification is required before delivering value. | Client callbacks never mark an order paid; backend verification, webhook, or reconciliation does. |
| https://paystack.com/docs/payments/webhooks/ | Verify x-paystack-signature with HMAC SHA-512 and handle retries. | Raw-body constant-time signature verification and transactional replay claims remain mandatory. |

## Actual Payment Flow

1. Mobile submits the order with an idempotency key.
2. Django validates ownership, pricing, coupon, service mode, addresses, and calculates the authoritative total.
3. Django initializes Paystack with backend-only credentials, a server reference, GHS amount in pesewas, and order/user metadata.
4. Django stores the Paystack access code and transaction reference on the one-to-one Payment.
5. Mobile reuses the payment intent returned by order creation. It does not initialize a second transaction.
6. Paystack returns to the backend HTTPS callback.
7. The callback redirects only to the registered connect-laundry mobile route with the reference.
8. The backend confirms payment through signed webhook, authenticated verify, or scheduled reconciliation.
9. Every confirmation path validates reference, amount, currency, metadata ownership, and live/test domain.
10. The same database transaction marks payment/order state and creates the idempotent laundry settlement ledger.
11. Mobile polls backend state when confirmation is asynchronous and offers receipt-based retry/recovery.

## Controls Proven Locally

- Secret key is backend-only.
- Amount comes from the frozen server order total.
- Decimal-safe major-to-minor conversion is used for charges, refunds, and transfers.
- Payment references are unique in the database.
- Pending sessions are reused and the order row is locked during initialization.
- Signed webhooks use raw request bytes, HMAC SHA-512, and constant-time comparison.
- Duplicate/replayed webhook events are claimed transactionally.
- Failed webhook processing rolls back the claim so Paystack can retry.
- Verify and webhook reject wrong reference, amount, currency, order metadata, user metadata, and live/test domain.
- Payment ownership is enforced for verify, status, and receipt.
- Refund requests are staff-only and settle asynchronously through signed webhooks.
- Webhook-first, verify-first, and reconciliation-first success paths create one settlement record.
- The mobile app keeps pending payments recoverable from order history/receipt.
- Non-cash orders cannot be accepted while payment is pending, and pending/success/refund-pending payments cannot be cancelled or rejected until verification or refund resolves.

## Edge-Case Result

| Scenario | Expected backend result |
| --- | --- |
| Duplicate order tap | Order idempotency returns the original result. |
| Duplicate payment start | Existing pending reference/access code is reused. |
| Mobile closes checkout | Order remains pending and receipt can resume payment. |
| Callback is lost | Signed webhook or 10-minute reconciliation confirms the payment. |
| App is killed after payment | Backend truth remains; order history/receipt refreshes state. |
| Webhook arrives first | Payment, order, and settlement commit once. |
| Verify arrives first | Payment, order, and settlement commit once; later webhook is a no-op. |
| Duplicate webhook | Replay ledger returns 200 without duplicate fulfillment. |
| Invalid signature | 401 and no state mutation. |
| Amount/currency/metadata/domain mismatch | Payment attempt is rejected, audited, and not fulfilled. |
| Paystack timeout during transfer | Outcome is marked indeterminate; transfer is not blindly retried. |
| Refund accepted | Payment becomes REFUND_PENDING; only webhook settlement makes it REFUNDED. |
| Cancel/reject races payment confirmation | Payment and order rows are locked; the lifecycle transition returns 409 until verification/refund resolves. |
| Accept unpaid non-cash order | Lifecycle returns 409; cash orders retain the existing acceptance path. |

## Evidence

- Focused backend payment and lifecycle regression suite: 38 passed on 2026-08-13.
- Canonical complete backend suite: 689 passed, 14 warnings on 2026-08-13.
- Django system check: no issues on 2026-08-13.
- Migration drift check: no changes detected on 2026-08-13.
- Mobile typecheck: passed on 2026-08-13.
- Mobile complete suite: 30 suites and 255 tests passed on 2026-08-13.
- OpenAPI generation: 0 errors and 3 enum-name warnings on 2026-08-13.

## Remaining Production Blockers

- Live and test Paystack dashboard settings have not been inspected from this workspace.
- A real low-value live transaction has not been executed.
- Deployed webhook delivery and retry evidence has not been captured.
- The production HTTPS callback and mobile deep-link return have not been tested on TestFlight/Play builds.
- The scheduled reconciliation task requires a running production Celery worker and beat process.
- A real refund lifecycle has not been exercised in live mode.
- A production settlement/payout reconciliation has not been compared with the Paystack dashboard.
- Live cancellation/refund operations still require a release-owner procedure; code now blocks contradictory order transitions until payment verification or refund resolves.

## Manual Paystack Actions for Philip

| Dashboard location | Exact field/action | Expected value | How to verify |
| --- | --- | --- | --- |
| Paystack Dashboard > Settings > API Keys & Webhooks | Live secret key | sk_live prefix, backend secret store only | Django deploy check does not emit payments.E002 |
| Paystack Dashboard > Settings > API Keys & Webhooks | Live public key | Matching pk_live prefix | Deploy check does not emit payments.E003 |
| Paystack Dashboard > Settings > API Keys & Webhooks | Live webhook URL | HTTPS production API /api/v1/payments/webhook/ | Signed charge.success reaches backend and returns 200 |
| Backend host environment | PAYSTACK_CALLBACK_URL | HTTPS production API /api/v1/payments/callback/ | Checkout returns through HTTPS and opens Simame |
| Backend host environment | PAYSTACK_APP_CALLBACK_URL | connect-laundry://orders/payment-callback | Production-signed app opens the callback route |
| Backend host environment | PAYMENT_CURRENCY | GHS | Charge, verify response, order, receipt, and settlement all show GHS |
| Paystack live transaction view | Reference/amount/domain | Same server reference and amount; domain live | Backend payment and order become paid once |
| Paystack webhook logs | Delivery/retry | 2xx for valid events; invalid signatures rejected | Duplicate event does not create duplicate history/settlement |
| Paystack refunds | Low-value test refund | Accepted then webhook-confirmed | SUCCESS to REFUND_PENDING to REFUNDED and settlement reverses |
| Paystack security | Secret-key IP whitelist, if host egress is stable | Only approved production IPv4 addresses | Backend API calls succeed only from approved host |

Do not paste live keys into chat, source files, mobile environment variables, issue trackers, or screenshots.