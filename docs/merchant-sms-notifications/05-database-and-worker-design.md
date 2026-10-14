# 05 — Database & Worker Design

## 1. Relational Schema & Models

All notification persistence resides in `admin_notifications/models.py`.

### 1.1 `AdminNotificationEvent`
Stores canonical business events emitted within database transactions.
```sql
CREATE TABLE admin_notifications_adminnotificationevent (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(40) NOT NULL,
    order_id UUID NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ NULL
);
CREATE INDEX admin_notif_event_order ON admin_notifications_adminnotificationevent (order_id, created_at);
```

### 1.2 `AdminNotificationDelivery`
Represents an individual message intent targeted at a specific audience and recipient.
```sql
CREATE TABLE admin_notifications_adminnotificationdelivery (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES admin_notifications_adminnotificationevent(id) ON DELETE CASCADE,
    audience VARCHAR(16) NOT NULL,   -- 'ADMIN' or 'MERCHANT'
    laundry_id UUID NULL REFERENCES laundries_laundry(id) ON DELETE CASCADE,
    channel VARCHAR(16) NOT NULL,    -- 'SMS', 'WHATSAPP', 'TELEGRAM'
    provider VARCHAR(32) NOT NULL,   -- 'arkesel_sms'
    template VARCHAR(32) NOT NULL,   -- 'BOOKING', 'CANCELLED', 'CHANGED'
    recipient VARCHAR(64) NOT NULL,  -- '233241234567'
    status VARCHAR(20) NOT NULL,     -- 'PENDING', 'SENDING', 'SUBMITTED', 'DELIVERED', etc.
    attempt_count SMALLINT NOT NULL DEFAULT 0,
    claimed_at TIMESTAMPTZ NULL,
    claim_token UUID NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at TIMESTAMPTZ NULL,
    delivered_at TIMESTAMPTZ NULL,
    failed_at TIMESTAMPTZ NULL,
    last_error_class VARCHAR(40) NOT NULL DEFAULT '',
    last_error_safe_message VARCHAR(300) NOT NULL DEFAULT '',
    provider_message_id VARCHAR(128) NOT NULL DEFAULT '',
    parts_total SMALLINT NOT NULL DEFAULT 1,
    parts_sent SMALLINT NOT NULL DEFAULT 0,
    estimated_segments INTEGER NOT NULL DEFAULT 0,
    dedup_key VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uniq_admin_notif_delivery_recipient UNIQUE (event_id, channel, recipient, audience)
);
CREATE INDEX admin_notif_deliv_status_next ON admin_notifications_adminnotificationdelivery (status, next_attempt_at);
CREATE INDEX admin_notif_laundry_recent ON admin_notifications_adminnotificationdelivery (laundry_id, created_at DESC);
```

### 1.3 `MerchantNotificationSettings`
Stores laundry-scoped notification configuration and phone verification state.
```sql
CREATE TABLE admin_notifications_merchantnotificationsettings (
    id BIGSERIAL PRIMARY KEY,
    laundry_id UUID NOT NULL UNIQUE REFERENCES laundries_laundry(id) ON DELETE CASCADE,
    sms_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    phone VARCHAR(20) NOT NULL DEFAULT '',
    phone_verified_at TIMESTAMPTZ NULL,
    verification_method VARCHAR(10) NOT NULL DEFAULT '', -- 'OTP' or 'ACCOUNT'
    pending_phone VARCHAR(20) NOT NULL DEFAULT '',
    otp_hash VARCHAR(128) NOT NULL DEFAULT '',
    otp_expires_at TIMESTAMPTZ NULL,
    otp_attempts SMALLINT NOT NULL DEFAULT 0,
    updated_by_id BIGINT NULL REFERENCES users_user(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 2. Horizontal Worker Architecture

```
                    +---------------------------+
                    |    PostgreSQL Database    |
                    |   (Outbox Deliveries)     |
                    +-------------+-------------+
                                  |
              +-------------------+-------------------+
              | SELECT ... FOR UPDATE SKIP LOCKED     |
              v                                       v
    +-------------------+                   +-------------------+
    |  Celery Worker 1  |                   |  Celery Worker 2  |
    |  (Pod / Host A)   |                   |  (Pod / Host B)   |
    +---------+---------+                   +---------+---------+
              |                                       |
              | POST /api/v2/sms/send                 | POST /api/v2/sms/send
              v                                       v
    +-----------------------------------------------------------+
    |                    Arkesel SMS Gateway                    |
    +-----------------------------------------------------------+
```

### 2.1 Concurrency Safety Guarantees
- **No Shared State**: Workers do not rely on in-memory counters or locks.
- **SKIP LOCKED Claim**: Each worker claims an isolated batch of up to 25 pending deliveries in a single sub-millisecond index scan.
- **Claim Token Leases**: When a worker claims a row, it writes a random UUID (`claim_token`) and timestamp (`claimed_at`). Subsequent outcome writes (SUBMITTED, FAILED) use:
  ```sql
  UPDATE admin_notifications_adminnotificationdelivery
  SET status = 'SUBMITTED', claim_token = NULL
  WHERE id = <id> AND claim_token = <token> AND status = 'SENDING';
  ```
  If a worker dies mid-send and its lease expires (10 minutes), the recovery job resets the row to `UNKNOWN`. The dead worker, if revived, cannot overwrite newer state.

---

## 3. Maintenance & Data Scrubbing

### 3.1 Unsent Expiry & Worker Crash Recovery
Runs automatically every dispatch pass or via Celery beat (`maintenance.py`):
1. **Unsent Expiry**: Deliveries in `PENDING` or `RETRY` older than `ADMIN_NOTIFICATION_MAX_AGE_HOURS` (12 hours) are marked `DEAD` with error class `EXPIRED`. Stale booking notifications are never delivered days late.
2. **Worker Lease Recovery**: Deliveries stuck in `SENDING` for longer than `ADMIN_NOTIFICATION_CLAIM_LEASE_SECONDS` (10 minutes) are transitioned to `UNKNOWN` with error class `WORKER_INTERRUPTED` and alerted on Sentry.

### 3.2 Automated PII Purging
In accordance with Ghana Data Protection Act 2012 (Act 843):
- Order snapshots contain customer personal data (names, phone numbers, GPS coordinates, delivery instructions).
- Once an event is settled and older than `ADMIN_NOTIFICATION_PAYLOAD_RETENTION_DAYS` (30 days), `maintenance.purge_old_payloads()` scrubs personal fields (`customer`, `pickup`, `delivery`, `notes`, `money`) leaving only the anonymized audit record and order UUID.
- Transient OTP send logs older than 90 days are deleted permanently.
