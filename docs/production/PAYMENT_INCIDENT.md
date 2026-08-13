# Simame Payment Incident Runbook

Date accessed for source guidance: 2026-08-13.

## Official Sources Used

- Paystack accept payments: https://paystack.com/docs/payments/accept-payments/
- Paystack Transactions API: https://paystack.com/docs/api/transaction/
- Paystack webhooks: https://paystack.com/docs/payments/webhooks/

## Production Rules

- Mobile must never contain `PAYSTACK_SECRET_KEY`.
- Backend initializes transactions and computes amount server-side.
- Backend verifies transaction status and amount before fulfilling an order.
- Webhooks must validate the `x-paystack-signature` HMAC before processing.
- Duplicate callbacks and duplicate webhooks must be idempotent.

## Incident Triage

1. Identify impacted payment references, order IDs, user IDs, and timestamps.
2. Freeze automatic fulfilment for suspicious references if payment integrity is uncertain.
3. Verify each reference with Paystack from the backend using the secret key.
4. Compare verified amount, currency, status, customer, and order ownership.
5. Reconcile order/payment state through an audited backend admin action.
6. Communicate customer impact through support channels.

## Stop Conditions

Stop release or rollback if the backend trusts frontend success callbacks, accepts mismatched amounts, processes unsigned webhooks, creates duplicate orders on retry, or cannot reconcile pending/successful payments.
## Reconciliation Order

Use the backend as the only payment authority:

1. Fetch the local Payment, Order, audit entries, webhook event, and settlement by reference.
2. Verify the reference through Paystack from the backend.
3. Compare reference, amount in pesewas, currency, domain, order metadata, and user metadata.
4. If Paystack is successful but local state is pending, run the audited reconciliation path.
5. Confirm exactly one settlement exists before allowing fulfillment or payout.
6. If the order is cancelled, rejected, or disputed, stop fulfillment and use the staff refund workflow.
7. Never edit a payment directly in the database as the first incident action.

The production scheduler must run payments.tasks.reconcile_pending_payments every 10 minutes. Alert if the latest successful run is more than 20 minutes old.

## Credential Exposure

The tracked example file previously contained a Clerk webhook secret-shaped value in git history, and the working tree contained an Expo access token-shaped value. Both were redacted on 2026-08-13. Rotate both credentials before any release; repository redaction does not invalidate credentials already issued by their providers.