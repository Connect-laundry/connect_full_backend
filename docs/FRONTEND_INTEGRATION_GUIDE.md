# FRONTEND INTEGRATION GUIDE — CONNECT LAUNDRY

This guide provides everything needed for the frontend developer to integrate the mobile application with the production-ready backend.

---

## 🚀 1. GLOBAL CONFIGURATION

- **Base URL**: `https://connect-full-backend.onrender.com/api/v1/`
- **Standard Response Envelope**:
  ```json
  {
    "status": "success",
    "message": "Operation successful.",
    "data": { ... }
  }
  ```
- **Error Format**:
  ```json
  {
    "status": "error",
    "message": "User with this email already exists.",
    "data": { "email": ["This field must be unique."] }
  }
  ```

---

## 🔑 2. AUTHENTICATION (JWT FLOW)

We use **SimpleJWT**. No Clerk or OTP verification is required.

### 2.1 Register Screen

- **Endpoint**: `POST /auth/register/`
- **Fields**: `email`, `phone`, `first_name`, `last_name`, `password`, `password_confirm`.
- **Logic**: Successful registration **auto-logs in** the user. You will receive `accessToken` and `refreshToken` immediately.
- **Screen State**: Redirect to "Home" upon success.

### 2.2 Login Screen

- **Endpoint**: `POST /auth/login/`
- **Fields**: `email`, `password`.
- **Logic**: Store `accessToken` and `refreshToken` securely (e.g., `SecureStore` in Expo).
- **Header**: Attach to all protected requests as `Authorization: Bearer <accessToken>`.

### 2.3 Profile & "Me" Screen

- **Endpoint**: `GET /auth/me/`
- **Logic**: Call this on app start to hydrate your global user state (Zustand/Redux).
- **Response**: Full user profile including `fullName`, `role`, and `addresses`.

### 2.4 Token Refresh (Silent)

- **Endpoint**: `POST /auth/token/refresh/`
- **Payload**: `{"refresh": "<refreshToken>"}`
- **Logic**: Use an Axios interceptor. If a request fails with `401`, attempt to refresh the token and retry the original request.

---

## 🏠 3. HOME & LAUNDRY DISCOVERY

### 3.1 Home Screen (Banners & Categories & Featured)

- **Special Offers**: `GET /support/home/special-offers/` (Returns promotional carousels/banners).
- **Categories**: `GET /laundries/categories/` (Returns service categories like Wash & Fold, Dry Cleaning to display as pills/chips).
- **Featured Laundries**: `GET /laundries/laundries/?is_featured=true` (Returns laundries manually curated by admins to be featured).
- **Recommended Laundries**: `GET /laundries/laundries/?recommended=true` (Returns laundries sorted by a computed weighted score based on their ratings and number of reviews).
- **Favorites**: `GET /laundries/favorites/` (Returns a list of laundries the user has favorited).

> **Important Image Note**: When rendering `Laundry` items on cards, **always use the `imageUrl` property** (e.g. `item.imageUrl`) to load the image, as it provides the fully-qualified absolute URL expected by mobile Image components (not the relative `image` field).

### 3.2 Discovery Screen (Search & Map)

- **Discovery**: `GET /laundries/laundries/`
- **Nearby Filter**: Use `?nearby=true&lat=X&lng=Y&radius=10` (Radius is in km, defaults to 10. Automatically triggers high-performance spatial search and sorts by strict proximity).
- **Logic**: If the user hasn't granted location permissions, fallback to a standard list.

### 3.3 Laundry Detail Screen

- **Endpoint**: `GET /laundries/{id}/`
- **Nested Data**: Includes `services` (Wash, Iron, Dry Clean), `reviews`, and `opening_hours`.
- **Favorites**: `POST /laundries/{id}/favorite/` (Toggles heart icon).

### 3.4 Laundry Object Schema (Reference)

When calling the list or detail endpoints, the Laundry object follows this structure:

```json
{
  "id": "uuid",
  "name": "Sparkle Cleaners",
  "description": "Premium service...",
  "imageUrl": "https://res.cloudinary.com/...",
  "location": "123 Accra St",
  "latitude": "5.603700",
  "longitude": "-0.187000",
  "priceRange": "$$",
  "rating": 4.5,
  "reviewsCount": 12,
  "isOpen": true,
  "isFavorite": false,
  "minOrder": "10.00",
  "deliveryFee": "5.00",
  "estimatedDelivery": "2 hours"
}
```

---

## 🛒 4. CHECKOUT & ORDERS

### 4.1 Catalog & Vendor Pricing

The backend uses a Vendor-Specific Pricing architecture.

1. **Global Item Catalog**: `GET /api/v1/booking/catalog/items/`
2. **Global Service Types**: `GET /api/v1/booking/catalog/services/`
3. **Vendor Specific Menu**: `GET /api/v1/laundries/laundries/{laundry_id}/services/`
   - **Important**: This returns the `price` and `estimated_duration` for that specific laundry.

### 4.2 Checkout Preview (Calculation)

Before the user clicks "Confirm Order", use this endpoint to show them the full price breakdown on the **Review Order** screen.

- **Endpoint**: `POST /api/v1/booking/calculate/`
- **Payload**:
  ```json
  {
    "laundry": "uuid",
    "items": [{ "item": "uuid", "service_type": "uuid", "quantity": 2 }]
  }
  ```
- **Response**:
  ```json
  {
    "status": "success",
    "data": {
      "items_total": "30.00",
      "delivery_fee": "5.00",
      "pickup_fee": "2.00",
      "tax": "1.50",
      "platform_fee": "0.60",
      "total": "39.10",
      "currency": "GHS"
    }
  }
  ```

### 4.3 Scheduling & Pickup Slots

To let users pick a pickup time on the **Review Order** screen:

- **Endpoint**: `GET /api/v1/booking/schedule/?laundry_id={laundry_uuid}`
- **Response**: Returns a list of `BookingSlot` objects.
  - `start_time`, `end_time`: The window for pickup.
  - `is_available`: Only show slots where this is `true`.

### 4.4 Order Creation (Mixed Cart Support)

Once the user confirms the details on the **Review Order** screen:

- **Endpoint**: `POST /api/v1/booking/create/`
- **Payload**:
  ```json
  {
    "laundry": "uuid",
    "pickup_date": "2023-10-27T10:00:00Z",
    "address": "123 Accra St",
    "items": [
      {
        "item": "uuid-for-shirt",
        "service_type": "uuid-for-wash-fold",
        "quantity": 2
      },
      {
        "item": "uuid-for-suit",
        "service_type": "uuid-for-dry-clean",
        "quantity": 1
      }
    ],
    "special_instructions": "Pick up at the gate"
  }
  ```
- **Logic**: You can mix items with different service types. The backend fetches the correct vendor prices and calculates the final total securely.

### 4.5 Payment Flow (Paystack)

1. **Initialize**: `POST /payments/initialize/` (Send `order_id`).
2. **Result**: Backend returns an `authorization_url`. Open it in an `InAppBrowser`.
3. **Verification**: After payment, call `GET /payments/verify/{reference}/`.

### 4.6 Order Tracking (Lifecycle)

- **Status List**: `PENDING`, `CONFIRMED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`.
- **Logic**: Use a progress stepper. Fetch `GET /orders/{id}/` for real-time status.

### 4.7 Ratings & Reviews

1. **Viewing Ratings**:
   - Headers return `rating` (Avg stars) and `reviewsCount`.
2. **Submitting a Review**:
   - **Endpoint**: `POST /api/v1/laundries/{laundry_id}/reviews/`
   - **Payload**: `{"rating": 5, "comment": "Great!"}`

---

## 📈 5. DASHBOARDS

### 5.1 Customer Profile

- **Address Management**: `GET/POST/DELETE /addresses/`.
- **Referrals**: `GET /referral/stats/` (Show "Invite Friends" earn logic).

### 5.2 Laundry Owner Dashboard

- **Stats**: `GET /dashboard/stats/` (Total revenue, active orders).
- **Order Management**: `GET /dashboard/orders/`.
- **Status Updates**: Call the lifecycle endpoints (e.g., `/lifecycle/{id}/mark-washed/`) to push orders through the pipeline.

### 5.3 Notifications & Inbox

- **List Notifications**: `GET /support/notifications/`
- **Unread Count**: `GET /support/notifications/unread-count/` (Useful for a notification badge on the bell icon).
- **Mark Single as Read**: `PATCH /support/notifications/{id}/mark-read/`
- **Mark All as Read**: `POST /support/notifications/mark-all-read/`
- **Logic**: Use the `type` field returned on each notification to determine the leading icon or tap action.

---

## 🛠️ FINAL CHECKLIST FOR FRONTEND

1. ✅ **Endpoints**: All base URLs are live.
2. ✅ **Auth**: Removed all Clerk SDKs. Use standard Fetch/Axios.
3. ✅ **Validation**: Handle `400` errors by displaying field-specific messages from the `data` object.
4. ✅ **Icons**: Use the `type` field in notifications to show the correct icon (Order update vs Promo).
