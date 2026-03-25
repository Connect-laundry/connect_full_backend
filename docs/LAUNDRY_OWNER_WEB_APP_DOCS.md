# Connect Laundry — Laundry Owner Web App: Complete API Reference

> **Base URL**: `https://connect-full-backend.onrender.com/api/v1`
>
> **Auth**: All endpoints except Register, Login, and Public Catalog require `Authorization: Bearer <accessToken>`.
>
> **Response Envelope**: Most responses use `{ "status": "success"|"error", "message": "...", "data": {...} }`.

---

## Table of Contents

1. [Authentication & Session](#1-authentication--session)
2. [Owner Profile](#2-owner-profile)
3. [Storefront (My Laundry)](#3-storefront-my-laundry)
4. [Opening Hours](#4-opening-hours)
5. [Dashboard: Stats & Earnings](#5-dashboard-stats--earnings)
6. [Dashboard: Orders](#6-dashboard-orders)
7. [Order Lifecycle Actions](#7-order-lifecycle-actions)
8. [Service Availability](#8-service-availability)
9. [Machines Inventory](#9-machines-inventory)
10. [Staff / Team Management](#10-staff--team-management)
11. [Customer CRM](#11-customer-crm)
12. [Payout & Bank Accounts](#12-payout--bank-accounts)
13. [Reviews](#13-reviews)
14. [Notifications](#14-notifications)
15. [Enum Reference](#15-enum-reference)
16. [Error Handling Guide](#16-error-handling-guide)
17. [Frontend Implementation Checklist](#17-frontend-implementation-checklist)

---

## 1. Authentication & Session

### 1.1 Register Owner

```
POST /auth/register/
```

**Request Body**:
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `email` | string | Yes | Must be unique across all users |
| `phone` | string | Yes | Must be unique, e.g. `"0551234567"` |
| `password` | string | Yes | Django's password validation applies (min 8 chars, not common) |
| `password_confirm` | string | Yes | Must match `password` exactly |
| `first_name` | string | Yes | |
| `last_name` | string | Yes | |
| `role` | string | Yes | Must be `"OWNER"` for dashboard access |

**201 Response**:
```json
{
  "accessToken": "eyJhbG...",
  "refreshToken": "eyJhbG...",
  "user": {
    "id": "uuid",
    "email": "owner@example.com",
    "fullName": "Test Owner",
    "role": "OWNER"
  }
}
```

**400 Errors**:
- `password_confirm` missing or doesn't match
- `email` or `phone` already registered
- `role` is not `CUSTOMER` or `OWNER`

---

### 1.2 Login

```
POST /auth/login/
```

**Request Body**: `{ "email": "...", "password": "..." }`

**200 Response**:
```json
{
  "accessToken": "eyJhbG...",
  "refreshToken": "eyJhbG...",
  "user": {
    "id": "uuid",
    "email": "owner@example.com",
    "fullName": "Test Owner",
    "role": "OWNER"
  }
}
```

**400 Errors**: `{ "detail": "Invalid email or password." }`

> **Important**: After 5 failed login attempts from the same IP, the account is temporarily locked by `django-axes`.

---

### 1.3 Logout

```
POST /auth/logout/
```

**Request Body**: `{ "refreshToken": "eyJhbG..." }`

**200 Response**: `{ "detail": "Successfully logged out." }`

---

### 1.4 Token Refresh

```
POST /auth/token/refresh/
```

**Request Body**: `{ "refresh": "eyJhbG..." }`

**200 Response**: `{ "access": "new-access-token..." }`

---

## 2. Owner Profile

### 2.1 Get Profile

```
GET /auth/me/
```

**200 Response**:
```json
{
  "user": {
    "id": "uuid",
    "email": "owner@example.com",
    "phone": "0551234567",
    "first_name": "Test",
    "last_name": "Owner",
    "fullName": "Test Owner",
    "avatar": null,
    "role": "OWNER",
    "addresses": [],
    "created_at": "2026-03-25T15:00:00Z"
  }
}
```

### 2.2 Update Profile

```
PATCH /auth/me/
```

**Writable fields**: `first_name`, `last_name`, `phone`, `avatar`

**Read-only**: `id`, `email`, `role`, `created_at`

---

## 3. Storefront (My Laundry)

### 3.1 Create Laundry

```
POST /laundries/dashboard/my-laundry/
```

> **Constraint**: Each owner can only have ONE laundry. Attempting to create a second returns `400`.

**Request Body**:
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | Yes | Business name |
| `description` | string | No | |
| `address` | string | Yes | Full street address |
| `city` | string | Yes | e.g. `"Accra"` |
| `latitude` | decimal | Yes | For customer discovery via PostGIS |
| `longitude` | decimal | Yes | |
| `phone_number` | string | Yes | Business phone |
| `delivery_fee` | decimal | No | Default: 0.00 |
| `pickup_fee` | decimal | No | Default: 0.00 |
| `min_order` | decimal | No | Minimum order value |
| `price_range` | string | No | `"$"`, `"$$"`, or `"$$$"` |
| `estimated_delivery_hours` | int | No | |
| `image` | file | No | Upload via multipart/form-data |
| `opening_hours` | array | No | See [Section 4](#4-opening-hours) |

**201 Response**:
```json
{
  "status": "success",
  "message": "Laundry storefront created successfully. It is now pending admin approval.",
  "data": {
    "id": "uuid",
    "name": "Sparkle Clean",
    "status": "PENDING",
    "statusDisplay": "Pending",
    "is_active": false,
    "opening_hours": [...]
  }
}
```

### 3.2 Get My Laundry

```
GET /laundries/dashboard/my-laundry/
```

Returns an array of the owner's laundries (usually 1).

**Response field list**: `id`, `name`, `description`, `image`, `imageUrl`, `address`, `city`, `latitude`, `longitude`, `phone_number`, `price_range`, `estimated_delivery_hours`, `delivery_fee`, `pickup_fee`, `min_order`, `is_featured`, `is_active`, `status`, `statusDisplay`, `opening_hours[]`, `created_at`, `updated_at`

### 3.3 Update Laundry

```
PATCH /laundries/dashboard/my-laundry/{id}/
```

Send only the fields you want to change. `opening_hours` can be included to replace the full schedule.

**Read-only fields** (cannot be changed): `id`, `is_featured`, `is_active`, `status`, `created_at`, `updated_at`

### 3.4 Toggle Store Open/Closed

```
PATCH /laundries/dashboard/my-laundry/{id}/toggle/
```

**Optional Body**: `{ "reason": "Vacation mode" }` — reason is stored when closing.

**200 Response**:
```json
{
  "status": "success",
  "message": "Store is now closed.",
  "data": { "id": "uuid", "is_active": false, "name": "Sparkle Clean" }
}
```

**400 Error**: Only `APPROVED` laundries can be toggled. `PENDING` or `REJECTED` laundries get:
```json
{ "status": "error", "message": "Cannot toggle a laundry with status 'PENDING'." }
```

---

## 4. Opening Hours

### 4.1 Get Hours

```
GET /laundries/dashboard/my-laundry/{id}/hours/
```

**200 Response**:
```json
{
  "status": "success",
  "data": [
    { "id": "uuid", "day": 1, "dayDisplay": "Monday", "opening_time": "08:00:00", "closing_time": "18:00:00", "is_closed": false },
    { "id": "uuid", "day": 7, "dayDisplay": "Sunday", "opening_time": "00:00:00", "closing_time": "00:00:00", "is_closed": true }
  ]
}
```

### 4.2 Replace All Hours

```
PUT /laundries/dashboard/my-laundry/{id}/hours/
```

Send the complete weekly schedule as an array. **This replaces all existing hours.**

**Day values**: `1`=Monday, `2`=Tuesday, `3`=Wednesday, `4`=Thursday, `5`=Friday, `6`=Saturday, `7`=Sunday

```json
[
  { "day": 1, "opening_time": "08:00", "closing_time": "18:00", "is_closed": false },
  { "day": 2, "opening_time": "08:00", "closing_time": "18:00", "is_closed": false },
  { "day": 7, "opening_time": "00:00", "closing_time": "00:00", "is_closed": true }
]
```

---

## 5. Dashboard: Stats & Earnings

### 5.1 Stats Summary (Counters)

```
GET /laundries/dashboard/stats/
```

**200 Response**:
```json
{
  "status": "success",
  "data": {
    "pending_count": 3,
    "confirmed_count": 5,
    "picked_up_count": 2,
    "delivered_count": 10,
    "total_orders": 20,
    "recent_orders": [
      {
        "id": "uuid",
        "order_no": "CN-ABCD1234",
        "customer_name": "Kofi Mensah",
        "status": "PENDING",
        "status_display": "Pending",
        "total_amount": "45.00",
        "created_at": "2026-03-25T10:00:00Z",
        "pickup_date": "2026-03-26",
        "delivery_date": "2026-03-27"
      }
    ],
    "recent_reviews": [
      {
        "id": "uuid",
        "userName": "Ama",
        "rating": 5,
        "comment": "Great service!",
        "date": "2026-03-24T14:00:00"
      }
    ]
  }
}
```

### 5.2 Earnings + Analytics + Sentiment

```
GET /laundries/dashboard/earnings/
```

**200 Response**:
```json
{
  "status": "success",
  "data": {
    "today": "120.00",
    "this_week": "450.00",
    "this_month": "1200.00",
    "total_revenue": "8500.00",
    "time_series": [
      { "date": "2026-03-20", "revenue": "150.00" },
      { "date": "2026-03-21", "revenue": "200.00" }
    ],
    "sentiment": {
      "total_reviews": 15,
      "positive_reviews": 13,
      "score": 86.7
    }
  }
}
```

**Frontend Usage**:
- `time_series` → render with a line/bar chart (last 12 days of data)
- `sentiment.score` → display as a percentage gauge (null if no reviews yet)
- `today` / `this_week` / `this_month` → summary cards

---

## 6. Dashboard: Orders

### 6.1 List Orders

```
GET /laundries/dashboard/orders/
```

Supports standard DRF pagination (`?page=1&page_size=20`).

**Each order object**:
| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `order_no` | string | Human-readable, e.g. `CN-ABCD1234` |
| `customer_name` | string | Customer's full name |
| `status` | string | Machine status code (see [Enums](#15-enum-reference)) |
| `status_display` | string | UI-friendly label, e.g. `"Pending"` |
| `total_amount` | decimal | Order total in GHS |
| `created_at` | datetime | ISO 8601 |
| `pickup_date` | date | Scheduled pickup |
| `delivery_date` | date | Scheduled delivery |

---

## 7. Order Lifecycle Actions

All lifecycle actions use `PATCH` and follow this pattern:

```
PATCH /booking/lifecycle/{order_id}/{action}/
```

**Request Body** (optional): `{ "reason": "...", "metadata": {} }`

**Success Response**:
```json
{
  "status": "success",
  "message": "Order marked as CONFIRMED",
  "data": { "id": "uuid", "status": "CONFIRMED" }
}
```

**Error Response** (invalid transition):
```json
{
  "status": "error",
  "message": "Invalid state transition",
  "data": { "current_status": "DELIVERED", "target_status": "CONFIRMED" }
}
```

### Available Actions

| Action | URL Path | From Status | To Status |
|--------|----------|-------------|-----------|
| Accept | `/accept/` | `PENDING` | `CONFIRMED` |
| Reject | `/reject/` | `PENDING` | `REJECTED` |
| Picked Up | `/mark-picked-up/` | `CONFIRMED` | `PICKED_UP` |
| Washing | `/mark-washed/` | `PICKED_UP` | `IN_PROCESS` |
| Out for Delivery | `/mark-out-for-delivery/` | `IN_PROCESS` | `OUT_FOR_DELIVERY` |
| Delivered | `/mark-delivered/` | `OUT_FOR_DELIVERY` | `DELIVERED` |
| Complete | `/complete/` | `DELIVERED` | `COMPLETED` |
| Cancel | `/cancel/` | `PENDING`/`CONFIRMED` | `CANCELLED` |

### Order Timeline (Audit Trail)

```
GET /booking/lifecycle/{order_id}/timeline/
```

Returns the full history of status changes for an order.

---

## 8. Service Availability

Toggle a specific service on/off for your laundry.

```
PATCH /laundries/dashboard/services/{service_id}/
```

**Body**: `{ "is_available": true }` or `{ "is_available": false }`

---

## 9. Machines Inventory

### 9.1 List Machines

```
GET /laundries/dashboard/machines/
```

**Response object per machine**:
| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | |
| `name` | string | e.g. `"Samsung Washer #1"` |
| `machine_type` | string | `WASHER` / `DRYER` / `IRONER` / `OTHER` |
| `typeDisplay` | string | `"Washing Machine"` / `"Dryer"` / etc. |
| `status` | string | `IDLE` / `BUSY` / `MAINTENANCE` / `OUT_OF_ORDER` |
| `statusDisplay` | string | `"Idle"` / `"In Use"` / etc. |
| `notes` | string | Maintenance notes |
| `created_at` | datetime | |
| `updated_at` | datetime | |

### 9.2 Register Machine

```
POST /laundries/dashboard/machines/
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | Yes | Max 100 chars |
| `machine_type` | string | No | Default: `WASHER`. Options: `WASHER`, `DRYER`, `IRONER`, `OTHER` |
| `notes` | string | No | |

### 9.3 Update Machine

```
PATCH /laundries/dashboard/machines/{id}/
```

### 9.4 Toggle Machine Status

```
PATCH /laundries/dashboard/machines/{id}/status/
```

**Body**: `{ "status": "BUSY" }`

Valid values: `IDLE`, `BUSY`, `MAINTENANCE`, `OUT_OF_ORDER`

### 9.5 Delete Machine

```
DELETE /laundries/dashboard/machines/{id}/
```

Returns `204 No Content`.

---

## 10. Staff / Team Management

### 10.1 List Staff

```
GET /laundries/dashboard/staff/
```

**Response object per staff**:
| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | |
| `name` | string | |
| `email` | string | |
| `phone` | string | |
| `role` | string | `MANAGER` / `WASHER` / `IRONER` / `DRIVER` / `RECEPTIONIST` |
| `roleDisplay` | string | Human-readable role name |
| `invite_status` | string | `PENDING` / `ACCEPTED` / `DECLINED` |
| `inviteStatusDisplay` | string | Human-readable status |
| `created_at` | datetime | |
| `updated_at` | datetime | |

### 10.2 Invite Staff Member

```
POST /laundries/dashboard/staff/invite/
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | Yes | Max 150 chars |
| `email` | string | Yes | Must be a valid email |
| `phone` | string | No | |
| `role` | string | Yes | One of: `MANAGER`, `WASHER`, `IRONER`, `DRIVER`, `RECEPTIONIST` |

**400 Errors**:
- `"You must create a laundry first."` — owner hasn't created a laundry yet
- `"This person has already been invited."` — duplicate email for this laundry

### 10.3 Change Staff Role

```
PATCH /laundries/dashboard/staff/{id}/role/
```

**Body**: `{ "role": "MANAGER" }`

### 10.4 Remove Staff Member

```
DELETE /laundries/dashboard/staff/{id}/
```

Returns `204 No Content`.

---

## 11. Customer CRM

### 11.1 Customer List (Aggregated)

```
GET /laundries/dashboard/customers/
```

**Response**: Sorted by `total_spent` descending.

```json
{
  "status": "success",
  "message": "5 customer(s) found.",
  "data": [
    {
      "user_id": "uuid",
      "email": "customer@example.com",
      "first_name": "Ama",
      "last_name": "Owusu",
      "phone": "0241234567",
      "order_count": 12,
      "total_spent": "560.00",
      "last_order_date": "2026-03-24T14:00:00Z"
    }
  ]
}
```

### 11.2 Customer Profile (Detailed)

```
GET /laundries/dashboard/customers/{user_id}/profile/
```

```json
{
  "status": "success",
  "data": {
    "user_id": "uuid",
    "email": "customer@example.com",
    "first_name": "Ama",
    "last_name": "Owusu",
    "phone": "0241234567",
    "order_count": 12,
    "total_spent": "560.00",
    "orders": [
      {
        "order_no": "CN-ABCD1234",
        "status": "DELIVERED",
        "total_amount": "45.00",
        "created_at": "2026-03-24T14:00:00"
      }
    ]
  }
}
```

---

## 12. Payout & Bank Accounts

### 12.1 List Bank Accounts

```
GET /payments/payouts/bank-account/
```

### 12.2 Link Bank Account

```
POST /payments/payouts/bank-account/
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `bank_name` | string | Yes | e.g. `"GCB Bank"` |
| `account_name` | string | Yes | Account holder name |
| `account_number` | string | Yes | Max 20 chars |
| `bank_code` | string | Yes | Paystack bank code, e.g. `"040"` |
| `is_primary` | boolean | No | Default: `false`. Setting `true` auto-demotes other accounts. |

**Response per account**: `id`, `bank_name`, `account_name`, `account_number`, `bank_code`, `is_primary`, `created_at`

### 12.3 Request Payout

```
POST /payments/payouts/request/
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `bank_account_id` | UUID | Yes | Must belong to the authenticated owner |
| `amount` | decimal | Yes | Must be <= available balance |
| `notes` | string | No | |

**Business Logic**: `available_balance = total_earned_from_orders - total_paid_out`

**400 Errors**:
- `"Insufficient balance. Available: 250.00"` — amount exceeds available balance
- `"No laundry found."` — owner hasn't created a laundry yet

**201 Response**:
```json
{
  "status": "success",
  "message": "Payout request submitted.",
  "data": {
    "id": "uuid",
    "bank_account": "uuid",
    "bank_account_display": "GCB Bank - 7890",
    "amount": "100.00",
    "currency": "GHS",
    "status": "PENDING",
    "reference": "PO-A1B2C3D4E5F6",
    "notes": "",
    "requested_at": "2026-03-25T15:00:00Z",
    "processed_at": null
  }
}
```

### 12.4 Payout History

```
GET /payments/payouts/history/
```

Returns an array of payout objects (same shape as above), sorted by most recent first.

**Payout Statuses**: `PENDING` → `PROCESSING` → `COMPLETED` or `FAILED`

---

## 13. Reviews

### 13.1 List Reviews for My Laundry

```
GET /laundries/dashboard/my-laundry/{id}/reviews/
```

Paginated (20 per page).

**Response per review**:
| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | |
| `userName` | string | Customer's first name |
| `rating` | integer | 1-5 |
| `comment` | string | |
| `date` | string | ISO 8601 datetime |

---

## 14. Notifications

```
GET /support/notifications/
```

Returns the owner's notifications (order updates, system messages, etc.).

---

## 15. Enum Reference

### Order Status
| Code | Display | Description |
|------|---------|-------------|
| `PENDING` | Pending | New order, awaiting laundry acceptance |
| `CONFIRMED` | Confirmed | Accepted by the laundry |
| `PICKED_UP` | Picked Up | Items collected from customer |
| `IN_PROCESS` | In Process | Washing/cleaning in progress |
| `OUT_FOR_DELIVERY` | Out for Delivery | Items being returned to customer |
| `DELIVERED` | Delivered | Items returned to customer |
| `COMPLETED` | Completed | Order finalized by laundry |
| `CANCELLED` | Cancelled | Cancelled by customer or laundry |
| `REJECTED` | Rejected | Not accepted by the laundry |

### Laundry Approval Status
| Code | Description |
|------|-------------|
| `PENDING` | Awaiting admin review (new laundries start here) |
| `APPROVED` | Visible to customers, can be toggled open/closed |
| `REJECTED` | Admin rejected; not visible to customers |

### Machine Type
`WASHER`, `DRYER`, `IRONER`, `OTHER`

### Machine Status
`IDLE`, `BUSY`, `MAINTENANCE`, `OUT_OF_ORDER`

### Staff Role
`MANAGER`, `WASHER`, `IRONER`, `DRIVER`, `RECEPTIONIST`

### Staff Invite Status
`PENDING`, `ACCEPTED`, `DECLINED`

### Payout Status
`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`

### Opening Hours Day
`1`=Monday, `2`=Tuesday, `3`=Wednesday, `4`=Thursday, `5`=Friday, `6`=Saturday, `7`=Sunday

---

## 16. Error Handling Guide

| HTTP Status | Meaning | Action |
|-------------|---------|--------|
| `400` | Validation failed or business rule violated | Show the `message` to the user |
| `401` | Token expired or missing | Redirect to login, or refresh the token |
| `403` | User role doesn't have permission | Ensure user is `OWNER` role |
| `404` | Resource not found | Handle gracefully (e.g. "Laundry not found") |
| `429` | Rate limited (too many requests) | Show "Please wait" and retry after delay |

**Standard Error Shape**:
```json
{
  "status": "error",
  "message": "Human-readable error message",
  "data": { "field_name": ["Validation error detail"] }
}
```

---

## 17. Frontend Implementation Checklist

- [ ] **Auth Interceptor**: Attach `Authorization: Bearer <token>` to every request via Axios/Fetch interceptor.
- [ ] **Token Refresh**: On `401`, attempt a silent refresh using the `refreshToken`. If that fails, redirect to login.
- [ ] **Role Guard**: After login, check `user.role === "OWNER"`. Block non-owners from the dashboard.
- [ ] **Onboarding Flow**: After registration, direct the owner to create their laundry profile.
- [ ] **Approval State**: Show a clear "Pending Approval" banner until `status === "APPROVED"`. Disable store toggle.
- [ ] **Live Orders**: Poll `GET /laundries/dashboard/orders/` every 30s (or use a WebSocket if available).
- [ ] **Lifecycle Stepper**: Build a visual order stepper using the [lifecycle actions](#7-order-lifecycle-actions).
- [ ] **Revenue Charts**: Use `time_series` from `/dashboard/earnings/` with Chart.js or Recharts.
- [ ] **Sentiment Gauge**: Display `sentiment.score` as a percentage. Show `null` state for new laundries.
- [ ] **Machine Dashboard**: Color-code machine cards by status (green=Idle, yellow=Busy, red=Maintenance).
- [ ] **Staff Board**: Group staff by role. Show invite status badges (Pending/Accepted/Declined).
- [ ] **CRM Table**: Sortable customer table by `total_spent` and `order_count`. Link to profile drill-down.
- [ ] **Payout Flow**: 3-step: (1) Link bank account, (2) Enter amount (validate against available balance from earnings), (3) Submit.
- [ ] **File Uploads**: Use `multipart/form-data` for the laundry `image` field (not JSON).

---

*Last Updated: March 25, 2026*
