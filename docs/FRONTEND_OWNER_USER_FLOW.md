# Connect Laundry - Owner Web App: End-to-End Frontend Flow & Implementation Guide

**Target Audience:** Frontend/Web Developer (Next.js/React preferred)
**Objective:** Provide a pixel-perfect roadmap of the user journey, state management rules, routing logic, and API integrations for the Connect Laundry Owner Dashboard.

---

## 1. Global Route Guards & State Management

Before building the UI, implement a strict routing architecture based on the user's current data state. The API returns the User and their associated Laundry profile.

### The 4 Core Access Gates (Route Guards)

1. **`GuestGuard`** (Only unauthenticated users):
   - Routes: `/login`, `/register`, `/forgot-password`
   - Logic: If an auth token exists in local storage/cookies, redirect to `/dashboard`.
2. **`AuthGuard`** (Requires valid Auth Token):
   - Routes: All protected routes.
   - Logic: If no token, redirect to `/login`.

3. **`SetupGuard`** (User registered, but no Business Profile created):
   - Route: `/onboarding/setup`
   - Logic: Call `GET /api/v1/laundries/dashboard/my-laundry/`. If it returns empty `[]`, force the user to this setup wizard. If they try to access `/dashboard`, redirect back to `/onboarding/setup`.

4. **`ApprovalGuard`** (Business profile created, awaiting Admin Approval):
   - Route: `/onboarding/pending`
   - Logic: If the laundry `status === 'PENDING'`, lock them in this waiting room. They cannot access the main dashboard or accept orders yet.

---

## 2. Phase 1: Authentication & Identity

### 2.1 Registration Flow (`/register`)
- **Action:** `POST /api/v1/auth/register/` (with `"role": "OWNER"`).
- **Payload:**
  ```json
  {
    "email": "owner@example.com",
    "phone": "+23354XXXXXXX",
    "first_name": "John",
    "last_name": "Doe",
    "role": "OWNER",
    "password": "...",
    "password_confirm": "..."
  }
  ```
- **Post-Action:** Save `accessToken`. Route directly to `/onboarding/setup`.

### 2.2 Login Flow (`/login`)
- **Action:** `POST /api/v1/auth/login/`
- **Post-Action Routing Logic:**
  1. Fetch Laundry Profile (`GET /api/v1/laundries/dashboard/my-laundry/`).
  2. If `[]` -> Redirect `/onboarding/setup`.
  3. If `status === 'PENDING'` -> Redirect `/onboarding/pending`.
  4. If `status === 'APPROVED'` -> Redirect `/dashboard`.

---

## 3. Phase 2: Business Profile Setup (The Setup Wizard)

_Route: `/onboarding/setup`_

### 📋 Required Fields (Mandatory)
The following fields **must** be present in your `POST` request to avoid a `400 Bad Request`:
- `name`: Business name (String)
- `address`: Full street address (Text)
- `city`: City name (String)
- `latitude`: Numeric coordinate (Decimal, e.g., 5.6037)
- `longitude`: Numeric coordinate (Decimal, e.g., -0.1870)
- `phone_number`: Business contact (String)

### 🖼️ Optional Fields
- `description`: Text
- `image`: File upload (Multipart/form-data)
- `price_range`: One of: `$`, `$$`, `$$$`
- `delivery_fee`, `pickup_fee`, `min_order`: Decimals
- `opening_hours`: Array of hour objects.

- **Endpoint**: `POST /api/v1/laundries/dashboard/my-laundry/` (multipart/form payload).
- **Success**: Redirect to `/onboarding/pending`.

---

## 4. Phase 3: The "Waiting Room" (Administrative Block)

_Route: `/onboarding/pending`_
- **UI:** A stunning 3D illustration of a folded shirt. Text: "We are reviewing your business profile."
- **Functionality:** 
  - Provide a "Refresh Status" button or poll `GET /api/v1/laundries/dashboard/my-laundry/`.
  - Once `status === 'APPROVED'`, redirect to `/dashboard`.

---

## 5. Phase 4: Core Dashboard Loop

_Route: `/dashboard`_

### 📊 Real-time Stats & Analytics
- **Stats:** `GET /api/v1/laundries/dashboard/stats/` (Total orders, pending counts, recent reviews).
- **Earnings:** `GET /api/v1/laundries/dashboard/earnings/` (Revenue data for charts).

### 🏷️ Catalog & Services
- **Update Status:** `PATCH /api/v1/laundries/dashboard/services/{id}/` (Toggle service active/inactive).
- **Go Live:** `PATCH /api/v1/laundries/dashboard/my-laundry/{id}/toggle/` (Sets `is_active: true`).

### 🛎️ Live Order Feed
- **Endpoint:** `GET /api/v1/laundries/dashboard/orders/`
- **Transitions (PATCH `/api/v1/orders/lifecycle/{id}/{action}/`):**
  - `accept`: Pending -> Confirmed
  - `mark-picked-up`: Confirmed -> Picked Up
  - `mark-washed`: Picked Up -> In Process
  - `mark-out-for-delivery`: In Process -> Out for Delivery
  - `mark-delivered`: Out for Delivery -> Delivered
  - `complete`: Delivered -> Completed

---

## 6. Phase 5: Financials & Payouts

_Route: `/dashboard/financials`_
- **Link Bank:** `POST /api/v1/payments/payouts/bank-account/`
- **Request Payout:** `POST /api/v1/payments/payouts/request/`
- **History:** `GET /api/v1/payments/payouts/history/`

---

## 7. Troubleshooting 400/401 Errors

### 🚫 If you get a `401 Unauthorized` / `403 Forbidden`:
1.  **Check Token**: Ensure you are sending `Authorization: Bearer <accessToken>`.
2.  **Check Role**: The user **must** have the `OWNER` role. Use `GET /api/v1/auth/me/` to verify. If `role` is `CUSTOMER`, you cannot create a laundry profile.

### 🚫 If you get a `400 Bad Request`:
1.  **Missing Fields**: Ensure all 6 mandatory fields (`name`, `address`, `city`, `latitude`, `longitude`, `phone_number`) are sent.
2.  **Duplicate Profile**: Each user can only have **one** laundry. If you already created one, use `PATCH` to update it instead of `POST`.
3.  **Data Types**: `latitude` and `longitude` must be valid numbers/decimals.

---

## 🛠️ Summary Checklist
1. [x] Configure `http://localhost:3000` in Backend CORS settings.
2. [ ] Implement JWT interceptor with `accessToken` standardized key.
3. [ ] Build the 6-stage order state machine action buttons.
4. [ ] Implement Setup Wizard with mandatory profile fields.
