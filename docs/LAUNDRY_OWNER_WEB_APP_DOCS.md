# Connect Laundry — Laundry Owner Web App Documentation

This document provides a comprehensive guide for the frontend developer to build the **Laundry Owner Web Application**. It covers authentication, dashboard metrics, order management, and service control.

---

## 🚀 1. Setup & Authentication

The Owner Web App uses the same **SimpleJWT** system as the mobile app. Users must have the `OWNER` role to access owner-specific endpoints.

- **Base URL**: `https://connect-full-backend.onrender.com/api/v1/`
- **Login Endpoint**: `POST /auth/login/`
  - Payload: `{"email": "owner@example.com", "password": "..."}`
  - Response: Includes `access`, `refresh` tokens, and `user.role` (which must be `OWNER`).
- **Profile Hydration**: `GET /auth/me/`
  - Use this to get the user's full details and verify their role on app launch.

---

## 📈 2. Dashboard & Analytics

The dashboard provides a high-level overview of the laundry's performance.

### 2.1 Statistics Summary

- **Endpoint**: `GET /laundries/dashboard/stats/`
- **Data Points**:
  - `pending_count`: Orders waiting for acceptance.
  - `confirmed_count`: Orders accepted but not yet picked up.
  - `picked_up_count`: Orders currently in possession.
  - `delivered_count`: Orders delivered.
  - `total_orders`: Lifetime total.

### 2.2 Earnings Overview

- **Endpoint**: `GET /laundries/dashboard/earnings/`
- **Data Points** (Returns revenue for orders in `DELIVERED` or `COMPLETED` status):
  - `today`: Total NGN for today.
  - `this_week`: Total NGN for the current week.
  - `this_month`: Total NGN for the current month.
  - `total_revenue`: Lifetime revenue.

---

## 📦 3. Order Management

Owners manage orders through their lifecycle using a dedicated dashboard view.

### 3.1 Order List (Owner Filtered)

- **Endpoint**: `GET /laundries/dashboard/orders/`
- **Pagination**: Supports standard offset-limit pagination.
- **Fields per Order**:
  - `order_no`: Human-readable ID (e.g., `CN-ABCD1234`).
  - `customer_name`: Full name of the user.
  - `status`: Machine status (e.g., `PENDING`).
  - `status_display`: UI-friendly status name (e.g., `Pending`).
  - `total_amount`: Order value.
  - `pickup_date` / `delivery_date`.

### 3.2 Order Lifecycle Actions

Base URL for all transitions: `/booking/lifecycle/{order_id}/`

| Action               | Path                      | Target Status      |
| :------------------- | :------------------------ | :----------------- |
| **Accept Order**     | `/accept/`                | `CONFIRMED`        |
| **Reject Order**     | `/reject/`                | `REJECTED`         |
| **Mark Picked-Up**   | `/mark-picked-up/`        | `PICKED_UP`        |
| **Mark Washed**      | `/mark-washed/`           | `IN_PROCESS`       |
| **Out for Delivery** | `/mark-out-for-delivery/` | `OUT_FOR_DELIVERY` |
| **Mark Delivered**   | `/mark-delivered/`        | `DELIVERED`        |
| **Mark Completed**   | `/complete/`              | `COMPLETED`        |

---

## ⚙️ 4. Laundry Settings & Services

Owners can manage their business status and available services.

### 4.1 Business Status (Deactivation)

- **Endpoint**: `PATCH /laundries/laundries/{id}/deactivate/`
- **Payload**: `{"reason": "Vacation mode"}`
- **Behavior**: Marks the laundry as `is_active = false`, hiding it from discovery.

### 4.2 Service Availability

Owners can toggle specific services (e.g., "Ironing") on/off without deleting them.

- **Endpoint**: `PATCH /laundries/dashboard/services/{id}/`
- **Payload**: `{"is_active": true/false}`

---

## 📝 5. Data Model Reference

### Order Status Flow

`PENDING` → `CONFIRMED` → `PICKED_UP` → `IN_PROCESS` → `OUT_FOR_DELIVERY` → `DELIVERED` → `COMPLETED`

### Laundry Object

- `name`, `description`, `image`, `address`.
- `phone_number`.
- `delivery_fee`, `min_order`.
- `price_range` (`$`, `$$`, `$$$`).

---

## 🛠️ Developer Checklist

1. [ ] Implement JWT Interceptor for `Authorization: Bearer <token>`.
2. [ ] Create a "Live Orders" dashboard polling `/dashboard/orders/`.
3. [ ] Build a "Lifecycle Stepper" for order detail views using the transition endpoints.
4. [ ] Implement a "Vacation Mode" toggle in settings.
