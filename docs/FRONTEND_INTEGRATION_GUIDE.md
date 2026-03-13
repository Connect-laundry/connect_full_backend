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
- **Featured Laundries (Recommended)**: `GET /laundries/featured/` (High-performance dedicated endpoint for featured laundries. Optimized with prefetching to avoid latency).
- **Featured (Alternative)**: `GET /laundries/laundries/?is_featured=true` (Also supported).
- **Recommended Laundries**: `GET /laundries/laundries/?recommended=true` (Returns laundries sorted by a computed weighted score based on their ratings and number of reviews).
- **Favorites**: `GET /laundries/favorites/` (Returns a list of laundries the user has favorited).

> **Important Image Note**: When rendering `Laundry` items on cards, **always use the `imageUrl` property** (e.g. `item.imageUrl`) to load the image, as it provides the fully-qualified absolute URL expected by mobile Image components (not the relative `image` field).

### 3.2 Discovery Screen (Search & Map)

- **Discovery**: `GET /laundries/laundries/`
- **Nearby Filter**: Use `?nearby=true&lat=X&lng=Y&radius=10`
  - `lat`, `lng`: User's current coordinates in decimal degrees (e.g., `5.6037`).
  - `radius`: Search radius in km (defaults to 10).
- **Logic**: The backend performs high-performance spatial search using PostGIS. If the user hasn't granted location permissions, fallback to a standard list.

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
  "estimated_delivery_hours": 24
}
```

> **Note**: `estimated_delivery_hours` is an integer representing hours. E.g. `24` means 1 day.

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
    "pickup_address": "123 Office St", // Optional for calculation
    "pickup_lat": 5.6037, // Optional
    "pickup_lng": -0.187, // Optional
    "delivery_address": "456 Home St", // Optional for calculation
    "delivery_lat": 5.6037, // Optional
    "delivery_lng": -0.187, // Optional
    "items": [
      {
        "item": "uuid", // IMPORTANT: Use 'itemId' from the services list
        "service_type": "uuid", // IMPORTANT: Use 'serviceTypeId'
        "quantity": 2
      }
    ]
  }
  ```
- **Note**: In the services list, `id` is the bridge record ID. Use **`itemId`** for the item and **`serviceTypeId`** for the service type in this payload.

- **Response (Now Flattened)**:
  ```json
  {
    "status": "success",
    "message": "Price breakdown calculated successfully.",
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
- **Response**:
  ```json
  {
    "status": "success",
    "message": "Available slots fetched successfully.",
    "data": [
      {
        "id": "slot-uuid",
        "start_time": "2023-11-01T08:00:00Z",
        "end_time": "2023-11-01T12:00:00Z",
        "is_available": true
      }
    ]
  }
  ```
- **Logic**: Only show slots where `is_available` is `true`.

### 4.4 Order Creation (Mixed Cart Support)

Once the user confirms the details on the **Review Order** screen:

- **Endpoint**: `POST /api/v1/booking/create/`
- **Payload**:

```json
{
  "laundry": "uuid",
  "pickup_date": "2023-10-27T10:00:00Z",
  "pickup_address": "123 Office St",
  "pickup_lat": 5.6037,
  "pickup_lng": -0.187,
  "delivery_address": "456 Home St",
  "delivery_lat": 5.6037,
  "delivery_lng": -0.187,
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
  "special_instructions": "Pick up from office reception, deliver to main gate at home",
  "payment_method": "paystack"
}
```

- **Logic**: You can mix items with different service types. The backend fetches the correct vendor prices and calculates the final total securely.

### 4.5 Payment Flow (Paystack)

1. **Initialize**: `POST /payments/initialize/` (Send `order_id`).
2. **Result**: Backend returns an `authorization_url`. Open it in an `InAppBrowser`.
3. **Verification**: After payment, call `GET /payments/verify/{reference}/`.

### 4.6 Order Tracking & Success Screen

- **Status List**: `PENDING`, `CONFIRMED`, `PICKED_UP`, `IN_PROCESS`, `OUT_FOR_DELIVERY`, `DELIVERED`, `COMPLETED`.
- **Logic**: 
    1. The `POST /api/v1/booking/create/` response returns the full `Order` object.
    2. Use the `id` from that response to immediately fetch full details or track status.
    3. **Order Detail Endpoint**: `GET /api/v1/orders/{id}/`
    4. **Response Fields**:
        - `order_no`: Human-readable ID (e.g. `ORD-12345`).
        - `delivery_date`: The calculated timestamp when the laundry will be ready.

> **Important**: Ensure you are calling `GET /api/v1/orders/{id}/`. Do **not** use the redundant `/orders/orders/` path.

### 4.7 Receipt & Order Summary (View Receipt)

The "View Receipt" screen should combine data from the **Order Detail** and the **Price Breakdown** endpoints.

#### 📄 Data Fields for the UI:

| Category | Unified Field Name | Description |
| :--- | :--- | :--- |
| **Header** | `order_no` | Human-readable ID (e.g. `ORD-8821`). |
| | `created_at` | Date/Time of order. |
| | `status` | Progress state (e.g. `CONFIRMED`). |
| **Laundry** | `laundryName` | Name of the shop. |
| **Logistics** | `pickup_date` | When the items will be picked up. |
| | `delivery_date` | **"Ready by"** - The estimated completion time. |
| | `pickup_address` | Physical pickup location. |
| | `delivery_address`| Physical delivery location. |
| **Items** | `items[]` | List including `name`, `service_type`, `quantity`, and `price`. |
| **Money** | `items_total` | Subtotal of all items. |
| | `discount` | Savings from coupon. |
| | `delivery_fee` | Cost of transport. |
| | `tax` | VAT/Tax amount. |
| | `total` | **Final amount paid.** |

> **Note**: For a professional look, ensure you show the `order_no` prominently at the top and the `delivery_date` clearly as the "Estimated Completion".

### 4.8 Ratings & Reviews

1. **Viewing Ratings**:
   - Headers return `rating` (Avg stars) and `reviewsCount`.
2. **Submitting a Review**:
   - **Endpoint**: `POST /api/v1/laundries/{laundry_id}/reviews/`
   - **Payload**: `{"rating": 5, "comment": "Great!"}`

---

## 📈 5. DASHBOARDS

### 5.1 Address Management

To provide a seamless delivery experience, users should save their physical locations.

- **List Addresses**: `GET /api/v1/addresses/`
- **Create Address**: `POST /api/v1/addresses/`
  - **Fields**:
    - `label`: String (e.g., "Home", "Office").
    - `address_line1`: String (Full street address).
    - `city`: String (Default: "Accra").
    - `latitude`, `longitude`: Decimal (Optional but highly recommended for the driver's map).
    - `is_default`: Boolean (If `true`, this becomes the primary address).
- **Update/Delete**: `PATCH/DELETE /api/v1/addresses/{id}/`
- **Supported Cities**: `GET /api/v1/addresses/supported-cities/` (Use this to restrict address entry to cities where you actually have laundries).

#### 💡 Professional UX: Google Places + Map Integration

To ensure accuracy and a "wow" factor, implement the following flow for both Pickup and Delivery addresses:

1. **Auto-Detect**: On screen load, use `expo-location` to get the user's current GPS. Show this on a **Map View** with a pin.
2. **Autocomplete Input**: Use the **Google Places Autocomplete SDK**. As the user types, they _must_ pick an address from the dropdown suggestions.
3. **Strict Validation**: Do not allow the user to type a random text. Force selection from the Google list. This ensures you always have the correct `address_string`, `latitude`, and `longitude`.
4. **Interactive Map**: If the user moves the map pin manually, perform **Reverse Geocoding** (Google Maps API) to update the text input automatically.
5. **Final Storage**: Save the validated `address_string`, `lat`, and `lng` to the backend `POST /api/v1/addresses/`.

6. **Checkout Selection**:
   - The User should be able to select TWO addresses on the review screen: **"Pickup From"** and **"Deliver To"**.
   - These should be sent as `pickup_address` and `delivery_address` in the `POST /api/v1/booking/create/` payload.
   - **"Same as Pickup" Logic**: Add a checkbox that, when checked, simply copies the `pickup_address` value into the `delivery_address` field before sending the request. This avoids double entry for the user.

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

```

```
