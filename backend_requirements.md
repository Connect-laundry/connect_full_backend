Connect Laundry Backend Requirements
This report details the remaining API endpoints and data models required by the frontend application, following the initial authentication implementation.

1. API Endpoints
   The following endpoints are categorized by functional domain.

🏠 Home & Discovery
GET /laundries/: List all laundries. Supports filtering by:
lat, lng & nearby=true: Spatial search for nearby laundries.
featured=true: List featured laundries for the home screen.
category: Filter by service type (e.g., "Wash & Fold", "Dry Cleaning").
search: Full-text search for laundry names.
GET /laundries/{id}/: Detailed information for a single laundry, including services, operating hours, and reviews.
GET /laundries/favorites/: List of laundry profiles favorited by the current user.
🧺 Booking & Services
GET /booking/services/: Global catalog of services (e.g., "Regular Wash", "Heavy Duty").
GET /booking/items/: List of items that can be laundered (e.g., "Shirt", "Trousers", "Duvet").
GET /booking/schedule/: Fetch available pickup and delivery time slots based on laundry availability.
POST /booking/create/: Initialize a booking process (draft order).
📦 Orders & Transactions
GET /orders/: List all orders for the authenticated user.
GET /orders/{id}/: Detailed status and items for a specific order.
POST /orders/: Finalize and place an order.
POST /orders/estimate/: (Optional but Recommended) Calculate price preview before final submission.
👤 Profile & Support
GET /auth/profile/: Retrieve currently logged-in user's profile details.
PATCH /auth/profile/: Update user profile (name, phone, default address).
POST /auth/logout/: Invalidate the current session/token.
/support/help/: Retrieve FAQ or help documentation.
/support/feedback/: Submit user feedback. 2. Data Models
The following structures represent the expected JSON responses and payloads.

Laundry Model
json
{
"id": "uuid",
"name": "string",
"image": "url",
"location": "string",
"distance": "string",
"rating": 4.5,
"reviewsCount": 120,
"address": "string",
"services": ["Wash & Fold", "Ironing"],
"isOpen": true,
"priceRange": "$$",
"isFavorite": false,
"estimatedDelivery": "24h",
"description": "string",
"phoneNumber": "string"
}
Review Model
json
{
"id": "uuid",
"userName": "string",
"rating": 5,
"comment": "string",
"date": "ISO-8601"
}
Order Model
json
{
"id": "uuid",
"orderNo": "#CN-12345",
"service": "Wash & Fold",
"date": "ISO-8601",
"status": "PENDING | WASHING | READY | DELIVERED | CANCELLED",
"paymentStatus": "PAID | UNPAID | REFUNDED",
"totalAmount": 150.00,
"items": [
{
"id": "uuid",
"name": "Shirt",
"quantity": 5,
"price": 10.00
}
]
} 3. Integration Priorities
Nearby Laundries: Critical for the Discovery/Home screen.
Laundry Details & Services: Necessary for users to view what they are booking.
Active Orders: Needed for the "Current Order" card on the Home screen.
Profile Management: Essential for user personalization.
