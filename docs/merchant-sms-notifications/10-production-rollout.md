# 10 — Production Rollout, Monitoring & Disaster Recovery

## 1. Staged Rollout Strategy

To protect production reliability, the laundry owner SMS system is rolled out in four progressive stages:

```
[ Stage 1: Sandbox & Mocked Local ] 
               │
               ▼
[ Stage 2: Staging Integration with Test Phone ]
               │
               ▼
[ Stage 3: Closed Pilot (Whitelist UUIDs) ]
               │
               ▼
[ Stage 4: General Production Availability ]
```

### 1.1 Stage 1: Sandbox Validation (Done)
- Controlled via `ARKESEL_SMS_SANDBOX = True`.
- Confirmed payload structure, GSM-7 formatting, and zero external costs.

### 1.2 Stage 2: Staging Single Test Merchant
- Whitelist a single verified test laundry owner number.
- Send test bookings across COD and Paystack card payments.
- Confirm actual physical handset receipt on MTN and Telecel SIM cards in Ghana.

### 1.3 Stage 3: Closed Pilot Rollout
- Configure `MERCHANT_SMS_PILOT_LAUNDRY_IDS = "<uuid-1>,<uuid-2>,<uuid-3>"`.
- Only approved pilot laundries generate merchant SMS deliveries.
- Non-pilot laundries silently bypass merchant SMS without affecting order creation.
- Monitor provider submission latency, callback arrival times, and owner feedback for 72 hours.

### 1.4 Stage 4: Full Production Rollout
- Clear `MERCHANT_SMS_PILOT_LAUNDRY_IDS` (empty allows all laundries).
- Verify `MERCHANT_SMS_NOTIFICATIONS_ENABLED = True`.
- Monitor error rates and Arkesel account balance.

---

## 2. Feature Flags & Operational Configuration

| Setting Name | Default | Staging / Development | Production | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `MERCHANT_SMS_NOTIFICATIONS_ENABLED` | `False` | `True` | `True` | Server-side master switch for merchant alerts. |
| `MERCHANT_SMS_PILOT_LAUNDRY_IDS` | `""` | `""` | Optional whitelist | Comma-separated laundry UUIDs for pilot testing. |
| `MERCHANT_SMS_DETAIL_MODE` | `'compact'`| `'compact'` | `'compact'` | Compact (masked PII) vs Full operational details. |
| `ARKESEL_SMS_SANDBOX` | `True` | `True` | `False` | Sandbox mode prevents real charges when true. |
| `ADMIN_NOTIFICATIONS_USE_CELERY` | `False` | `False` | `True` | Directs outbox kicks to Celery worker queue. |
| `SELLER_CENTER_BASE_URL` | `""` | `https://seller-staging...`| `https://seller.simame.tech`| Origin included in SMS deep-links. |

---

## 3. Production Monitoring & Alerting

SIMAME uses Sentry and structured Django logging for real-time operational visibility:

### 3.1 Key Metrics & Sentry Tags
- `admin_notification.submitted`: Successful acceptance by Arkesel.
- `admin_notification.delivered`: Handset delivery verified via webhook.
- `admin_notification.failed`: Permanent error (bad key, invalid recipient).
- `admin_notification.blocked`: Suppressed due to unverified or missing phone.
- `admin_notification.retry`: Transient failure scheduled for backoff.
- `admin_notification.dead`: Message expired unsent after 12 hours.
- `admin_notification.unknown`: Timeout during send where outcome is ambiguous.

### 3.2 High-Priority Alert Conditions
- **Fatal Alert**: `ARKESEL BALANCE EXHAUSTED` (HTTP 402 or balance response) -> Triggers immediate SMS/email to platform administrator.
- **Error Alert**: Provider 5xx error rate > 5% over 5-minute window.
- **Warning Alert**: Unsent backlog in `PENDING` > 100 items older than 5 minutes.

---

## 4. Emergency Kill Switch & Rollback Procedures

### 4.1 Instant Kill Switch (Zero Code Deploy)
To immediately halt all merchant SMS dispatches without taking down the application or booking flows:
```bash
# In Render / Cloud Environment Dashboard
MERCHANT_SMS_NOTIFICATIONS_ENABLED=False
```
**Effect**:
- Dispatcher immediately stops claiming merchant deliveries.
- Orders continue creating and paying normally.
- Zero customer or merchant disruptions.

### 4.2 Database Rollback
If migrations ever need to be rolled back:
```bash
python manage.py migrate admin_notifications 0001
```
(No destructive schema migrations were introduced; backward-compatible columns ensure smooth rollback).
