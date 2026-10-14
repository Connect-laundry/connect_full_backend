# 01 — Existing Architecture Discovery & SMS Integration Analysis

## 1. Executive Summary

This document establishes the baseline architecture of SIMAME's notifications, booking lifecycles, and existing Arkesel SMS integrations across the three repositories:
- Backend: `C:\projects\CONNECT\connect_new_backend` (Django 6.0.5, Python 3.14.3)
- Web (Seller Center & Admin): `C:\projects\CONNECT\Connect-Web-App` (Next.js 16.3.8, React 19.2.4)
- Customer Mobile: `C:\projects\CONNECT\connect-customer-mobile` (Expo SDK 54, React Native 0.81.5)

The primary finding is that SIMAME already possessed a battle-hardened, production-grade notification infrastructure within the `admin_notifications` Django application. Rather than introducing a disparate or competing SMS subsystem, the laundry owner notification feature has been cleanly unified onto this shared, proven transaction-outbox foundation.

---

## 2. Inventory of Existing Infrastructure

### 2.1 Backend Notification App (`connect_new_backend/admin_notifications`)
The `admin_notifications` app implements a durable transactional outbox pattern designed for zero lost alerts and zero HTTP request blocking:
- **Models (`models.py`)**:
  - `AdminNotificationEvent`: Durable record of business events (`NEW_ORDER`, `PAYMENT_CONFIRMED`, `ORDER_CANCELLED`, `ORDER_RESCHEDULED`, `PICKUP_LOCATION_CHANGED`, `DELIVERY_LOCATION_CHANGED`). Stores JSON snapshot payloads with customer names, masked phones, addresses, and order references.
  - `AdminNotificationDelivery`: Individual audience-specific communication intent (`audience`: `ADMIN` vs `MERCHANT`; `channel`: `SMS`, `WHATSAPP`, `TELEGRAM`; `status`: `PENDING`, `SENDING`, `SENT`, `SUBMITTED`, `DELIVERED`, `UNDELIVERED`, `BLOCKED`, `RETRY`, `FAILED`, `DEAD`, `UNKNOWN`). Enforces unique constraints on `[event, channel, recipient, audience]`.
  - `AdminNotificationProviderMessage`: Tracks individual parts of multipart SMS messages and maps provider message IDs (`sms_id`) to delivery records for webhook reconciliation.
  - `MerchantNotificationSettings`: Laundry-scoped settings table storing the verified SMS destination phone, verification status (`phone_verified_at`), OTP challenge state, and master alert toggle (`sms_enabled`).
  - `MerchantSmsSendLog`: Audit log tracking hourly OTP requests and daily test SMS dispatches to enforce rate limits.
- **Provider Adapters (`providers/`)**:
  - `providers/arkesel_sms.py`: HTTP client implementing Arkesel SMS API v2 (`POST https://sms.arkesel.com/api/v2/sms/send`). Handles authentication via the `api-key` header, sender ID configuration (`SIMAME`), payload construction, response parsing, and sandbox mode.
  - `providers/arkesel_whatsapp.py`: Arkesel KOVA IQ API integration for operations WhatsApp alerts.
  - `providers/telegram.py`: Official Telegram Bot API integration for ops alerts.
- **Dispatcher & Outbox Services (`services/`)**:
  - `services/outbox.py`: Emits notification events within database transactions using Django's `transaction.on_commit(kick_dispatch)`.
  - `services/dispatcher.py`: Background worker service claiming pending deliveries using `SELECT ... FOR UPDATE SKIP LOCKED`, preventing concurrent double-sends across multiple processes.
  - `services/kick.py`: Threadpool worker and post-request sweep mechanism providing zero-broker fallback when Redis/Celery is unavailable.
  - `services/merchant.py`: Core business logic resolving laundry owners, normalizing phone numbers, formatting GSM-7 templates, managing OTP lifecycle, and enforcing privacy boundaries.
  - `services/callbacks.py`: Idempotent delivery report processor reconciling Arkesel webhook status updates.
  - `services/maintenance.py`: Batch reconciliation of missing delivery receipts and automated data scrubbing of customer PII after retention periods.

### 2.2 Environment Variables & Secrets
Secrets are strictly managed server-side via `django.conf.settings` and read at runtime through `admin_notifications.conf`:
- `ARKESEL_API_KEY`: Server-side Arkesel v2 API key.
- `ARKESEL_SENDER_ID`: Approved alphanumeric sender ID (defaults to `SIMAME`).
- `ARKESEL_SMS_BASE_URL`: Base URL (default `https://sms.arkesel.com`).
- `ARKESEL_SMS_SANDBOX`: Boolean flag. Evaluates to `True` in development/staging and `False` in production.
- `ARKESEL_SMS_CALLBACK_SECRET`: Secret token embedded in delivery report URLs (`/api/v1/notifications/arkesel-sms-status/<token>/`).
- `ARKESEL_SMS_CALLBACK_BASE_URL`: Public HTTPS backend origin for Arkesel webhooks.
- `MERCHANT_SMS_NOTIFICATIONS_ENABLED`: Master feature flag gating laundry owner SMS dispatch.
- `MERCHANT_SMS_PILOT_LAUNDRY_IDS`: Comma-separated list of Laundry UUIDs for staged pilot rollout.
- `MERCHANT_SMS_DETAIL_MODE`: `'compact'` (default privacy mode) vs `'full'`.

---

## 3. The Existing Booking & Payment Flow

Prior to merchant SMS extension, the booking flow operated as follows:
1. **Customer Creates Booking (`/api/v1/orders/create/`)**:
   - Customer submits laundry items, pricing mode, pickup/delivery addresses, and payment method (`CASH` vs `CARD`/`MOBILE_MONEY`).
   - `Order` record created in `PENDING` status.
   - For Cash on Delivery (`CASH`), order transitions immediately to `UNPAID` and emits `NEW_ORDER`.
   - For Card / Mobile Money (`CARD`), Paystack transaction initialized; order remains pending payment.
2. **Payment Confirmation (`/api/v1/payments/webhook/paystack/`)**:
   - Paystack sends cryptographic webhook with `charge.success`.
   - Webhook handler verifies HMAC-SHA512 signature, sets `order.payment_status = PAID`, and emits `PAYMENT_CONFIRMED`.
3. **Outbox Event Creation**:
   - Inside the booking/payment transaction, `outbox.emit_new_order` or `outbox.emit_payment_confirmed` creates an `AdminNotificationEvent` and child `AdminNotificationDelivery` rows.
4. **Outbox Kick**:
   - `transaction.on_commit(kick_dispatch)` triggers the dispatcher in the background after the transaction commits to PostgreSQL.
5. **Worker Execution**:
   - Dispatcher claims rows via `SKIP LOCKED`, contacts Arkesel API v2, stores provider `sms_id`, and marks delivery as `SUBMITTED`.
6. **Arkesel Delivery Webhook**:
   - Arkesel posts back handset delivery state (`DELIVERED`, `NOT_DELIVERED`, `EXPIRED`) to `/api/v1/notifications/arkesel-sms-status/<token>/`.

---

## 4. Architectural Gaps Identified & Resolved

1. **Owner Recipient Resolution**:
   - *Problem*: Admin alerts previously routed only to hardcoded ops numbers (`ADMIN_SMS_RECIPIENTS`). Laundry owners did not have a dedicated verified recipient resolution mechanism.
   - *Fix*: Created `MerchantNotificationSettings` and `services.merchant.resolve_recipient(laundry)` strictly binding alerts to the owner's verified phone number with fallback blocking.
2. **Premature Pay-First Alerts**:
   - *Problem*: Notifying merchants on `NEW_ORDER` for card/MoMo bookings would cause "ghost" notifications for abandoned checkout screens.
   - *Fix*: Implemented `is_pay_first(order)` logic: COD and custom quote notify on `NEW_ORDER`; pay-first bookings notify only upon `PAYMENT_CONFIRMED`.
3. **Celery Worker Scalability**:
   - *Problem*: Background processing relied entirely on an in-process single-thread executor (`kick.py`), which is sufficient for low volume but cannot scale horizontally across cluster nodes under high concurrency.
   - *Fix*: Added `admin_notifications.tasks.dispatch_notifications_task` and Celery queue routing with safe brokerless fallback.
4. **Seller Center Management UI**:
   - *Problem*: Laundry owners had no UI to view their notification status, verify phone numbers, send test alerts, or monitor delivery logs.
   - *Fix*: Built `SmsBookingAlertsCard` in Next.js Seller Center (`Connect-Web-App`) integrated into `NotificationsTab.tsx`.
