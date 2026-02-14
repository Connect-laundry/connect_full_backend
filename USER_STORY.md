# User Story: The Journey of a Laundry Order

This document explains how your backend powers the Connect Laundry application by following a user, **Kwame**, as he uses the app.

---

## 1. Onboarding & Trust

**Kwame, a new user, downloads the app.**

- **Action**: He signs up using his phone number.
- **Backend**: The `users` app handles this via **Clerk Authentication**. We verify his identity and create a `User` record in PostgreSQL.
- **Action**: He uploads a profile picture.
- **Backend**: The `MediaUploadView` receives the image. Because we are in production, it securely uploads it to **Cloudinary** and saves the URL to his profile.

## 2. Discovery

**Kwame wants to find a laundry service nearby.**

- **Action**: He grants location permission and searches for "Ironing".
- **Backend**: The `laundries` app uses **PostGIS** (Geospatial database) to find laundries within 10km of his coordinates. It filters ensuring they are "Open" and sorts them by rating.

## 3. Creating an Order

**Kwame chooses "Sparkle Cleaners" and adds 5 shirts to his basket.**

- **Action**: He taps "Estimate Price".
- **Backend**: The `ordering` app calculates the total:
  - `5 x Shirt Price`
  - `+ Delivery Fee` (calculated by `logistics` app based on distance)
  - `+ Service Fee`
  - `- Discount` (if he has a valid coupon from the `marketplace` app).
- **Result**: The backend returns `NGN 4,500`.

## 4. Payment

**Kwame is happy with the price and proceeds to pay.**

- **Action**: He selects "Pay with Card".
- **Backend**: The `payments` app talks to **Paystack**. It creates a transaction and sends back a secure authorization URL.
- **Action**: Kwame enters his card details on the Paystack popup.
- **Backend**: Paystack calls our **Webhook**. The backend uses a "Lock" system to ensure we process this exactly once. We verify the signature, confirm the money is received, and officially mark the Order as `CONFIRMED`.

## 5. Fulfillment (The Lifecycle)

**The laundry service gets to work.**

- **Action**: The Laundry Manager sees the new order.
- **Backend**: The `ordering` app moves the status through the lifecycle: `PENDING` -> `CONFIRMED` -> `PROCESSING` -> `READY_FOR_DELIVERY` -> `DELIVERED`.
- **Notification**: At each step, the `marketplace` app can trigger notifications (via email or push) to keep Kwame updated.

## 6. Feedback & Growth

**Kwame receives his clean clothes.**

- **Action**: He rates the service 5 stars.
- **Backend**: The `laundries` app updates the `Review` table and recalculates the average rating for "Sparkle Cleaners".
- **Action**: He shares his referral code `KWAME20` with a friend.
- **Backend**: The `users` app tracks this. When his friend orders, Kwame gets a reward recorded in the `ReferralStats`.

---

## System Health (Behind the Scenes)

While all this is happening:

- **Celery**: Is running background tasks (like sending emails) so the app never freezes.
- **Sentry**: Is watching for errors. if a bug happens, it alerts the dev team instantly.
- **Cloudinary**: Is serving optimized images so the app uses less data for Kwame.
- **PostgreSQL**: Is safely storing every transaction, ensuring no data is ever lost.
