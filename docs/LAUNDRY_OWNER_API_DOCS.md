# Laundry Owner API Handbook — Connect Laundry (v1.0)

This documentation is the **Source of Truth** and **Technical Contract** between the Connect Laundry Backend and the Frontend Developer building the Laundry Owner Web App.

---

## 🚀 1. GLOBAL INTEGRATION STANDARDS

- **Base URL**: `https://connect-full-backend.onrender.com/api/v1/`
- **Authentication**: Bearer JWT (`Authorization: Bearer <accessToken>`).
- **Standard Response Envelope**:
  ```json
  {
    "status": "success",
    "message": "Operation successful.",
    "data": { ... }
  }
  ```
- **Error Format (RFC 7807-lite)**:
  ```json
  {
    "status": "error",
    "message": "Validation failed.",
    "data": {
       "field_name": ["List of errors for this specific field."]
    }
  }
  ```

---

## 🔑 2. AUTHENTICATION & IDENTITY (IAM)

### 2.1 Owner Registration
- **Endpoint**: `POST /auth/register/`
- **Payload**:
  ```json
  {
    "email": "kofi@cleaners.com",
    "phone": "+233201234567",
    "first_name": "Kofi",
    "last_name": "Mensah",
    "password": "SecurePassword123!",
    "role": "OWNER"
  }
  ```
- **Behavior**: Auto-logs in the user. Returns `access` and `refresh` tokens + `user` object.

### 2.2 Login & Session
- **Login**: `POST /auth/login/` (Fields: `email`, `password`).
- **Token Refresh**: `POST /auth/token/refresh/` (Field: `refresh`).
- **Me (Hydrate State)**: `GET /auth/me/` (Returns full owner profile, shop IDs, and avatar).

### 2.3 Profile & Brand Updates
- **Update Profile**: `PATCH /auth/me/`
- **Upload Avatar (Brand Logo)**: `PATCH /auth/me/`
  - **Header**: `Content-Type: multipart/form-data`
  - **Field**: `avatar` (File)

### 2.4 Recovery
- **Request Link**: `POST /auth/forgot-password/` (`email`).
- **Complete Reset**: `POST /auth/reset-password/` (`token`, `new_password`, `confirm_password`).

---

## 🏪 3. SHOP & ASSET MANAGEMENT

### 3.1 Storefront Config (GIS, Hours, Pricing)
- **Fetch Settings**: `GET /laundries/{id}/`
- **Update Core Config**: `PATCH /laundries/{id}/`
  - **Payload**:
    ```json
    {
      "is_active": true,
      "latitude": 5.6037,
      "longitude": -0.187,
      "price_per_kg": "12.50",
      "min_order": "20.00",
      "delivery_fee": "5.00",
      "pricing_method": "PER_KG"
    }
    ```

### 3.2 Inventory Tracking (Machines)
- **List Machines**: `GET /laundries/{id}/machines/`
- **Add Machine**: `POST /laundries/{id}/machines/`
  - **Payload**: `{"name": "Washer #1", "machine_type": "WASHER", "status": "IDLE"}`
- **Update Status**: `PATCH /laundries/{id}/machines/{machine_id}/` (e.g., set to `MAINTENANCE`).

### 3.3 Operating Hours Manager
- **Update Days**: `POST /laundries/{id}/hours/`
  - **Payload**:
    ```json
    [
      {"day": 1, "opening_time": "08:00:00", "closing_time": "18:00:00", "is_closed": false},
      {"day": 7, "is_closed": true}
    ]
    ```

---

## 📦 4. OPERATIONAL ORDER MANAGEMENT

### 4.1 Master Order List (Dashboard)
- **Endpoint**: `GET /dashboard/orders/`
- **Query Params**:
  - `status`: `PENDING`, `CONFIRMED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`.
  - `search`: Filter by `order_no` or customer email.
- **Payload Schema**:
  ```json
  "results": [
    {
      "id": "uuid",
      "order_no": "ORD-123",
      "status": "PENDING",
      "pickup_date": "2023-11-01T10:00:00Z",
      "user": { "fullName": "John Doe", "phone": "..." }
    }
  ]
  ```

### 4.2 Order Detail & Status Audit
- **Endpoint**: `GET /api/v1/orders/{id}/`
- **Frontend Logic**: This contains the `status_history` array (Audit Trail) and the detailed `price_breakdown`.

### 4.3 The Weighing Pivot (Per-Kg Mode)
For orders where `pricing_method == 'PER_KG'`, the frontend **must** prompt the owner to enter the `actual_weight` before moving the order to `IN_PROCESS`.
- **Endpoint**: `PATCH /api/v1/orders/{id}/weigh/`
- **Payload**: `{"actual_weight": 5.5}`
- **Behavior**: Updates the `final_price` and notifies the customer to pay the balance.

### 4.4 Lifecycle State Transitions
Use these `POST` endpoints to push the order through the pipeline.
- **Accept**: `POST /api/v1/lifecycle/{id}/accept/`
- **Reject**: `POST /api/v1/lifecycle/{id}/reject/` (Payload: `{"reason": "..."}`)
- **Mark Picked Up**: `POST /api/v1/lifecycle/{id}/mark-picked-up/` (Usually by Driver)
- **Start Processing**: `POST /api/v1/lifecycle/{id}/mark-washed/`
- **Out for Delivery**: `POST /api/v1/lifecycle/{id}/mark-out-for-delivery/` (Payload: `{"driver_id": "uuid"}`)
- **Delivered**: `POST /api/v1/lifecycle/{id}/mark-delivered/`
- **Complete**: `POST /api/v1/lifecycle/{id}/complete/`

---

## 💰 5. FINANCIALS & PAYOUTS

### 5.1 Dashboard Stats (Liquidity)
- **Stats**: `GET /api/v1/dashboard/stats/` (Pending vs Completed order volume).
- **Earnings**: `GET /api/v1/dashboard/earnings/`
  - **Data**: `{"today": "550.00", "week": "3500.00", "payout_account": "..."}`

### 5.2 Bank Account Management
Owners must link a bank for settlements.
- **Link Bank**: `POST /api/v1/payments/payouts/bank-account/`
  - **Payload**: `{"bank_name": "...", "account_number": "...", "bank_code": "..."}`
- **List Banks**: `GET /api/v1/payments/payouts/bank-account/`

### 5.3 Payout Requests
Move shop funds to the linked bank account.
- **Create Request**: `POST /api/v1/payments/payouts/request/`
  - **Payload**: `{"amount": 1000.00, "bank_account_id": "uuid"}`
- **Status Check**: `GET /api/v1/payments/payouts/history/`

---

## 🚛 6. WORKFORCE & LOGISTICS

### 6.1 Driver Assignment
Owners can manually assign orders to drivers for pick-up and delivery.
- **Endpoint**: `POST /logistics/assignments/`
- **Payload**:
  ```json
  {
    "order_id": "uuid",
    "driver_id": "uuid",
    "assignment_type": "PICKUP"
  }
  ```
- **Type**: `PICKUP` or `DELIVERY`.

---

## 🙋‍♂️ 7. MARKETPLACE & SUPPORT

### 7.1 Real-Time Notifications
- **Inbox**: `GET /api/v1/support/notifications/`
- **Mark Read**: `PATCH /api/v1/support/notifications/{id}/mark-read/`

### 7.2 FAQ & Reference
- **Retrieve Support Docs**: `GET /api/v1/support/faqs/`
- **Submit Feedback**: `POST /api/v1/support/help/feedback/` (`subject`, `message`).

---

## 📚 8. CONSTANTS & ENUMS (Source of Truth)

| Enum Type | Values |
| :--- | :--- |
| **Order Status** | `PENDING`, `CONFIRMED`, `REJECTED`, `PICKED_UP`, `WEIGHED`, `AWAITING_FINAL_PAYMENT`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`, `CANCELLED` |
| **Payment Status** | `PAID`, `UNPAID`, `PARTIALLY_PAID`, `REFUNDED` |
| **Payout Status** | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` |
| **Machine Status** | `IDLE`, `BUSY`, `MAINTENANCE`, `OUT_OF_ORDER` |
| **Staff Role** | `MANAGER`, `WASHER`, `IRONER`, `DRIVER`, `RECEPTIONIST` |
| **Pricing Method** | `PER_ITEM`, `PER_KG` |

---

## 📋 FRONTEND IMPLEMENTATION CHECKLIST

- [ ] Implement **`POST /auth/login/`** as the primary entry point.
- [ ] Ensure **`actual_weight`** entry is enforced for `PER_KG` orders.
- [ ] Build a **`Machine Management`** view for monitoring shop assets.
- [ ] Integrate **`Paystack Payouts`** by collecting Bank Codes and Account Numbers.
- [ ] Display the **`OrderStatusHistory`** timeline from the Detail view.
