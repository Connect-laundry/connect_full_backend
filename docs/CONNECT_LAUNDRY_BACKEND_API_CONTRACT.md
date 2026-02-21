# CONNECT LAUNDRY — BACKEND API CONTRACT

Version: 1.0
Status: Production Ready
Base URL: `https://connect-full-backend.onrender.com/api/v1/`

---

## SECTION 1 — AUTHENTICATION

### 1.1 Verify Clerk Token (Exchange)

Exchange a Clerk Frontend JWT for backend-specific SimpleJWT tokens.

- **URL**: `/auth/clerk/verify/`
- **Method**: `POST`
- **Auth required**: Yes (Clerk JWT in `Authorization: Bearer <token>`)
- **Headers required**: `Content-Type: application/json`
- **Request Body**: None (Token is parsed from Header)
- **Response Schema (Success 200)**:

```json
{
  "status": "success",
  "message": "Token verified successfully",
  "data": {
    "access": "jwt_access_token_string",
    "refresh": "jwt_refresh_token_string",
    "user": {
      "id": "uuid",
      "email": "user@example.com",
      "role": "CUSTOMER",
      "fullName": "John Doe"
    }
  }
}
```

- **Response Schema (Error 401)**:

```json
{
  "status": "error",
  "message": "Invalid or expired Clerk token",
  "data": {}
}
```

### 1.2 Token Refresh

- **URL**: `/auth/token/refresh/`
- **Method**: `POST`
- **Auth required**: No
- **Request Body**: `{"refresh": "refresh_token_string"}`
- **Response Success**: `{"access": "new_access_token"}`

### 1.3 Logout

- **URL**: `/auth/clerk/logout/`
- **Method**: `POST`
- **Auth required**: Yes
- **Request Body**: `{"refresh": "refresh_token_string"}`
- **Behavior**: Blacklists the refresh token.

---

## SECTION 2 — USER & PROFILE

### 2.1 Get My Profile

- **URL**: `/auth/clerk/me/`
- **Method**: `GET`
- **Response**: Returns standard `ProfileSerializer` data (email, phone, fullName, role, addresses).

### 2.2 Profile Management (PATCH)

- **URL**: `/auth/profile/`
- **Method**: `PATCH`
- **Payload**: `{"first_name": "...", "last_name": "...", "avatar": File}`

### 2.3 Address Management (AddressViewSet)

- **Base URL**: `/addresses/`
- **Endpoints**:
  - `GET /` : List all my addresses.
  - `POST /` : Create new address (`label`, `address_line1`, `city`, `latitude`, `longitude`, `is_default`).
  - `PATCH /{id}/` : Update address.
  - `DELETE /{id}/` : Delete address.

### 2.4 Referrals

- **Apply Referral Code**: `POST /referral/apply/` (Payload: `{"referral_code": "..."}`).
- **Referral Stats**: `GET /referral/stats/` (Returns `referral_code`, `total_referrals`, `earnings`).

---

## SECTION 3 — LAUNDRY DISCOVERY

### 3.1 Explore Laundries (List)

- **URL**: `/laundries/`
- **Method**: `GET`
- **Query Parameters**:
  - `nearby=true` : Activates spatial filtering.
  - `lat` & `lng` : Required if `nearby=true`.
  - `radius` : Search radius in KM (Default: 10).
  - `search` : Search by name or description.
  - `price_range` : Filter by tier (1-4).
- **Pagination**: Standard DRF Pagination (`results`, `count`, `next`, `previous`).

### 3.2 Laundry Detailed View

- **URL**: `/laundries/{id}/`
- **Method**: `GET`
- **Features**: Returns nested `services`, `reviews`, and `opening_hours`.

---

## SECTION 4 — SERVICES

### 4.1 Update Service Visibility (Owner Only)

- **URL**: `/dashboard/services/{id}/`
- **Method**: `PATCH`
- **Payload**: `{"is_active": true/false}`
- **Logic**: Allows owner to temporarily hide a service from their laundry profile.

---

## SECTION 5 — FAVORITES & REVIEWS

### 5.1 Toggle Favorite

- **URL**: `/laundries/{id}/favorite/`
- **Method**: `POST`
- **Behavior**: If the laundry is already a favorite, it is removed. Otherwise, it is added.

### 5.2 Submit Review

- **URL**: `/{laundry_id}/reviews/`
- **Method**: `POST`
- **Payload**: `{"rating": int(1-5), "comment": "string"}`

---

## SECTION 6 — ORDERS & LIFECYCLE

### 6.1 Create Order (Checkout)

- **URL**: `/booking/create/`
- **Method**: `POST`
- **Payload**:

```json
{
  "laundry": "uuid",
  "items": [{ "item": "uuid", "quantity": 2 }],
  "slot": "uuid",
  "coupon": "string (optional)",
  "notes": "string"
}
```

### 6.2 Price Breakdown (Checkout Preview)

- **URL**: `/orders/{id}/price-breakdown/`
- **Method**: `GET`
- **Response**: Returns `subtotal`, `delivery_fee`, `service_fee`, `discount_amount`, `total`.

### 6.3 State Transitions (PATCH)

Base URL: `/lifecycle/{order_id}/`

| Transition           | URL Path                  | Required Role  | Description                    |
| :------------------- | :------------------------ | :------------- | :----------------------------- |
| **Accept**           | `/accept/`                | OWNER/ADMIN    | PENDING -> CONFIRMED           |
| **Reject**           | `/reject/`                | OWNER/ADMIN    | PENDING -> REJECTED            |
| **Pick-up**          | `/mark-picked-up/`        | RIDER/OWNER    | CONFIRMED -> PICKED_UP         |
| **Washed**           | `/mark-washed/`           | OWNER          | PICKED_UP -> IN_PROCESS        |
| **Out for Delivery** | `/mark-out-for-delivery/` | RIDER/OWNER    | IN_PROCESS -> OUT_FOR_DELIVERY |
| **Delivered**        | `/mark-delivered/`        | RIDER/OWNER    | OUT_FOR_DELIVERY -> DELIVERED  |
| **Complete**         | `/complete/`              | OWNER          | DELIVERED -> COMPLETED         |
| **Cancel**           | `/cancel/`                | CUSTOMER/OWNER | PENDING -> CANCELLED           |

---

## SECTION 7 — PAYMENTS (PAYSTACK)

### 7.1 Initialize Payment

- **URL**: `/payments/initialize/`
- **Method**: `POST`
- **Payload**: `{"order_id": "uuid"}`
- **Response**: `{"authorization_url": "...", "reference": "..."}`

### 7.2 Verify Payment

- **URL**: `/payments/verify/{reference}/`
- **Method**: `GET`
- **Behavior**: Syncs with Paystack API. On success, moves order to `CONFIRMED`.

### 7.3 Webhook (Internal)

- **URL**: `/payments/webhook/` (Exposed for Paystack IPNs)

---

## SECTION 8 — NOTIFICATIONS

- **List Notifications**: `GET /support/notifications/` (Supports `?is_read=false`).
- **Mark One Read**: `PATCH /notifications/{id}/mark-read/`.
- **Mark All Read**: `POST /notifications/mark-all-read/`.

---

## SECTION 9 — DASHBOARD (OWNER)

- **Owner Stats**: `GET /dashboard/stats/` (Pending, Confirmed, Delivered counts).
- **Earnings**: `GET /dashboard/earnings/` (Revenue for Today, Week, Month).
- **Laundry Orders**: `GET /dashboard/orders/` (Owner-filtered order list).

---

## SECTION 10 — ADMIN ENDPOINTS

- **Laundries Management**: `PATCH /admin/laundries/{id}/approve/` or `/reject/`.
- **Service Oversight**: `PATCH /admin/services/{id}/approve/`.

---

## SECTION 11 — ERROR FORMAT STANDARD

All errors follow this envelope:

```json
{
  "status": "error",
  "message": "Descriptive error message",
  "data": {}
}
```

---

## SECTION 12 — GLOBAL TECHNICAL CONTRACT

- **Authentication**: Bearer JWT (`Authorization: Bearer <token>`).
- **Pagination**: Offset-Limit style (`count`, `next`, `previous`, `results`).
- **Enums**:
  - **OrderStatus**: `PENDING`, `CONFIRMED`, `REJECTED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`, `CANCELLED`.
  - **Roles**: `CUSTOMER`, `OWNER`, `DRIVER`, `ADMIN`.
- **Precision**: 2 decimal places for all currency (NGN).
- **Throttling**: Burst limit for user actions (100 req/min), standard for Auth (5 req/min).

---

## SECTION 13 — EDGE CASE MATRIX

- **Expired session**: Server returns `401` with `code: "token_not_valid"`.
- **Duplicate payment**: `PaymentInitializeView` returns `400` if a success record exists for the order.
- **Double state jump**: State machine blocks invalid transitions (e.g., `PENDING` -> `DELIVERED` directly).
