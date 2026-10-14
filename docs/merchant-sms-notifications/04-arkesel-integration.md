# 04 — Arkesel SMS API v2 Integration Specification

## 1. Official API Contract & Authentication

SIMAME integrates with the Arkesel SMS API v2:
- **Endpoint**: `POST https://sms.arkesel.com/api/v2/sms/send`
- **Authentication**: Custom HTTP header `api-key`
- **Content Type**: `application/json`

### 1.1 Request Headers
```http
POST /api/v2/sms/send HTTP/1.1
Host: sms.arkesel.com
api-key: <ARKESEL_API_KEY>
Content-Type: application/json
User-Agent: SIMAME-NotificationSystem/2.0
```

### 1.2 Request Payload Schema
```json
{
  "sender": "SIMAME",
  "message": "SIMAME: NEW BOOKING #SM4821\nCustomer: Ama M.\nPhone: 024 XXX 4567\nPickup: Ayeduase Gate, Today 4:00 PM\nItems: 2x Shirt, 1x Jeans\nTotal: GH₵45.00 (Cash on Delivery)\nManage in Seller Center: https://seller.simame.com/orders/SM4821",
  "recipients": ["233241234567"],
  "sandbox": false,
  "callback_url": "https://api.simame.com/api/v1/notifications/arkesel-sms-status/<token>/"
}
```

---

## 2. Response Parsing & Error Classification

Arkesel returns standardized JSON responses. SIMAME's provider adapter maps these into normalized internal outcome classes:

### 2.1 Success Response (HTTP 200)
```json
{
  "status": "success",
  "data": [
    {
      "recipient": "233241234567",
      "id": "c1f7b0a8-3d4e-4b2a-8f1e-9a7c6d5e4b3a"
    }
  ]
}
```
Mapped to `base.ACCEPTED` with `provider_message_id = "c1f7b0a8-3d4e-4b2a-8f1e-9a7c6d5e4b3a"`.

### 2.2 Error Classification Table

| HTTP Status | Arkesel Response Indicator | Internal Error Class | Classification | Handling & Retry Policy |
| :--- | :--- | :--- | :--- | :--- |
| **401 Unauthorized** | Invalid API key | `AUTHENTICATION_FAILED` | Permanent | Alert Sentry (`fatal`). Do NOT retry. |
| **402 Payment Required**| Insufficient balance / Low balance | `INSUFFICIENT_BALANCE` | Permanent | Alert Sentry (`fatal`). Suppress further calls until topped up. |
| **403 Forbidden** | Sender ID unapproved / inactive | `GATEWAY_INACTIVE` | Permanent | Alert ops (`error`). Do NOT retry. |
| **422 Unprocessable** | Malformed recipient / bad format | `INVALID_RECIPIENT` | Permanent | Mark delivery `FAILED`. Do NOT retry. |
| **429 Too Many Req** | Rate limit exceeded | `RATE_LIMITED` | Retryable | Backoff exponentially (30s, 120s, 600s, 1800s). |
| **500, 502, 503, 504**| Server errors / gateway timeout | `PROVIDER_5XX` | Retryable | Backoff exponentially. |
| **Timeout (Connect)**| Connection refused or timed out | `CONNECT_TIMEOUT` | Retryable | Retry with backoff up to 6 max attempts. |
| **Timeout (Read)** | Server took >15s to reply after send | `READ_TIMEOUT` | Unknown | Mark as `UNKNOWN`. Avoid duplicate send risk. |

---

## 3. Delivery Status Webhooks

Arkesel sends HTTP POST callbacks when handset delivery updates occur.

### 3.1 Webhook Contract
- **Endpoint**: `/api/v1/notifications/arkesel-sms-status/<token>/`
- **Method**: POST or GET (accepts both JSON payload and query parameters)
- **Parameters**:
  - `sms_id`: Provider message ID returned during submission.
  - `status`: Delivery status string (`DELIVERED`, `SUBMITTED`, `NOT_DELIVERED`, `EXPIRED`, `FAILED`).
  - `delivered_at`: Timestamp (optional).

### 3.2 State Reconciliation & Transition Logic
```python
FINAL = ('DELIVERED', 'NOT_DELIVERED', 'FAILED', 'EXPIRED')

def apply_sms_status(sms_id: str, status: str) -> str:
    # 1. Lookup provider message by provider_message_id
    message = ProviderMessage.objects.filter(provider_message_id=sms_id).first()
    if not message:
        return 'unknown'

    # 2. Prevent downgrade: Once DELIVERED, racing QUEUED cannot overwrite
    if message.provider_status == 'DELIVERED':
        return 'already_delivered'

    # 3. Update message status
    message.provider_status = status
    message.status_updated_at = timezone.now()
    message.save()

    # 4. Update parent delivery
    delivery = message.delivery
    if status == 'DELIVERED':
        delivery.status = Delivery.Status.DELIVERED
        delivery.delivered_at = timezone.now()
    elif status in ('NOT_DELIVERED', 'FAILED'):
        delivery.status = Delivery.Status.UNDELIVERED
        delivery.failed_at = timezone.now()
    delivery.save()
    return 'updated'
```

---

## 4. Message Templates & GSM-7 Segment Optimization

All messages are strictly sanitized to standard GSM-7 characters to prevent unexpected segment multiplication and Ghana cedi credit waste.

### 4.1 Compact Template (Default Privacy Mode)
```
SIMAME: NEW BOOKING #{order_no}
Cust: {customer_name}
Phone: {phone_masked}
Pickup: {pickup_summary}
Items: {items_summary}
Total: GH₵{total} ({payment_label})
Manage: {seller_url}
```
- Fits within **1 to 2 GSM-7 segments** (≤ 306 characters).
- Masks customer phone (`024 XXX 4567`) and truncates addresses to neighborhood/landmark.

### 4.2 Full Template (Operational Mode)
- Provides full phone and detailed delivery instructions for authorized operations.
- Automatically chunked if exceeding 6 concatenated segments (918 characters).
