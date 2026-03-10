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

### 4.1 Catalog & Vendor Pricing (New Architecture)

The backend now uses a Vendor-Specific Pricing architecture. Global items do not have fixed prices. Instead, each laundry sets its own menu.

1. **Global Item Catalog**: `GET /api/v1/booking/catalog/items/`
   - Returns all launderable items without prices (e.g. "Men's Shirt").
2. **Global Service Types**: `GET /api/v1/booking/catalog/services/`
   - Returns service categories (e.g. "Wash & Fold", "Dry Cleaning").
3. **Vendor Specific Menu**: `GET /api/v1/laundries/laundries/{laundry_id}/services/`
   - **Crucial step**: Call this endpoint when a user selects a laundry to see the actual tailored menu, prices, and exact availability for that specific vendor.
   - Example Response: `[{ "price": "15.00", "estimated_duration": "24 hours", "is_available": true, "item": { "id": "...", "name": "Shirt" }, "service_type": { "name": "Wash & Fold" } }]`

### 4.2 Order Creation

- **Endpoint**: `POST /booking/create/`
- **Payload**:
  ```json
  {
    "laundry": "uuid",
    "items": [{ "item": "uuid", "quantity": 2 }],
    "slot": "uuid",
    "notes": "Pick up at the gate"
  }
  ```

### 4.2 Payment Flow (Paystack)

1. **Initialize**: `POST /payments/initialize/` (Send `order_id`).
2. **Result**: Backend returns an `authorization_url`. Open this in a `WebView` or `InAppBrowser`.
3. **Verification**: Once the user finishes payment, call `GET /payments/verify/{reference}/` to confirm success.

### 4.3 Order Tracking (Lifecycle)

- **Status List**: `PENDING`, `CONFIRMED`, `REJECTED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`.
- **Logic**: Use a progress stepper UI. Poll `GET /orders/{id}/` every 30 seconds for real-time status updates.

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

Walkthrough: Authentication Overhaul & Production Release
We have successfully migrated the backend from an external Clerk/OTP system to a production-ready, unified JWT authentication system. The backend is now live and fully functional.

🏁 Key Accomplishments

1. Simplified Authentication System
   Clerk Removal: Eliminated all dependencies on the external Clerk service.
   OTP/2FA Deletion: Removed the complex OTP flow to provide immediate user access upon registration.
   Direct JWT: Implemented standard accessToken/
   refreshToken
   flow using SimpleJWT.
   Auto-Verification: New users are marked as is_verified = True by default.
2. Database & Schema Resolution
   Missing Migrations: Fixed critical ProgrammingError issues by manually creating migrations for
   SpecialOffer
   and the min_order field in the
   Laundry
   model.
   User Model: Cleaned up legacy fields and enforced immediate verification logic.
3. Production Deployment (Render)
   Zero-Downtime Migration: Successfully deployed the new system to https://connect-full-backend.onrender.com.
   Dependency Fixes: Resolved psycopg environment issues in the Render build process.
   🎥 Demonstration
   Live URL: https://connect-full-backend.onrender.com
   Admin Panel: https://connect-full-backend.onrender.com/admin/
   📄 Documentation Handover
   I have prepared two critical documents for your frontend developer:

FRONTEND_INTEGRATION_GUIDE.md
: A complete roadmap of every API endpoint, request schemas, and guidance on how to build the corresponding mobile screens.
Testing Guide
: A step-by-step Postman guide to verify the new auth flow.
✅ Final Status
All backend APIs are ready for integration. The system is simple, secure, and scalable.
