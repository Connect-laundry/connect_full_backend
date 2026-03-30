# User Story: Connect Laundry Owner Web Application

## Persona

- **Name**: Kofi Mensah
- **Role**: Laundry Business Owner (Verified Vendor)
- **Business**: "Kofi’s Premium Cleaners" (Mid-sized neighborhood service)
- **Tech Proficiency**: Moderate (Comfortable with Digital Dashboards)
- **Primary Device**: Desktop/Laptop (Command Center) & Tablet (Shop Floor)

---

## 1. Identity & Access Management (IAM)

### Registration & Vendor Onboarding

Kofi begins his journey by registering on the Connect Laundry platform. He enters his essential business details: email, phone number, and name.
- **Unique Identity**: Behind the scenes, the system assigns a UUID (Universally Unique Identifier) to Kofi, ensuring his data remains secure and portable throughout the platform's lifecycle.
- **Onboarding Wizard**: Upon registration, Kofi is guided through a multi-step setup to define his business profile, requiring administrative vetting (e.g., business license verification) before he can "Go Live."

### Secure Authentication (JWT)

Kofi logs in using his verified credentials.
- **Stateless Security**: The platform utilizes **JSON Web Tokens (JWT)**, which provides him with a secure, stateless session. This eliminates the friction of frequent re-logins while maintaining high-grade security for his business data.
- **Role-Based Access Control (RBAC)**: The system instantly recognizes his `OWNER` role, granting him exclusive access to financial reports, staff management, and shop settings that are hidden from `DRIVER` or `CUSTOMER` roles.

### Account Protection & Recovery

If Kofi forgets his password, he initiates a secure reset.
- **Audit-Traceable Reset**: The system generates a cryptographically hashed, time-limited token and delivers a reset link to his email via a background task (Celery). This ensures that even in cases of server congestion, his recovery request is prioritized and reliably delivered.
- **Profile Self-Service**: From his profile settings, Kofi can update his "Avatar" (brand logo), modify contact details, and review active sessions to ensure his account hasn't been compromised.

---

## 2. Command Center (Dashboard)

## 2. Command Center (Dashboard)

### Real-Time Performance Analytics

The dashboard is Kofi’s high-level command center, delivering live updates from the database without manual refresh.
- **KPI Snapshot**: He monitors **Total Revenue** (aggregated from `COMPLETED` orders), **Pending Acceptance** (new incoming requests), and **Active Workflows** (orders currently `IN_PROCESS`).
- **Trend Visualization**: A 7-day volume chart helps Kofi predict peak times (e.g., Sunday evenings) so he can adjust staffing.
- **System Intelligence**: If the platform's background workers detect a high volume of unassigned orders, Kofi sees a priority alert in his sidebar.

---

## 3. Storefront Meta-Data & Config

Kofi fine-tunes his digital shop to ensure high conversion and operational clarity:

### Geospatial Presence (GIS)
Kofi sets his shop’s precise coordinates using a map picker.
- **Precision Targeting**: The system stores **Latitude and Longitude** (Decimal format), allowing the marketplace algorithm to accurately calculate distance-based delivery fees and surface his shop to customers in supported cities (e.g., Accra, Lagos).

### Dynamic Operating Hours
Kofi defines his availability for each day of the week (Monday–Sunday).
- **Time Slots**: He sets specific `opening_time` and `closing_time`.
- **Vacation Mode**: He can toggle `is_closed` for specific days or his entire shop, instantly removing his listing from the customer app to prevent "Ghost Orders."

### Specialized Asset Tracking (Machines)
Kofi registers his physical laundry machines to monitor the shop floor’s pulse.
- **Machine Inventory**: He adds Washers, Dryers, and Ironing Presses.
- **Status Monitoring**: He toggles their status between `IDLE`, `BUSY`, or `MAINTENANCE`. This allows for future capacity planning based on available hardware.

### The Pricing Architecture
Connect Laundry offers a flexible pricing engine which Kofi manages:
- **Pricing Method Toggle**: Kofi chooses between **Per-Item** (fixed prices per shirt/suit) or **Per-Kg** (weight-based pricing).
- **Service Catalog**: He maps global items (e.g., "Duvet") to his shop with custom price snapshots and estimated durations (e.g., "Ready in 48 hours").
- **Logistics Fees**: He defines a **Minimum Order Value**, a **Base Delivery Fee**, and a **Pickup Fee** to protect his margins on small orders.

---

## 4. Advanced Order Lifecycle

## 4. Operational Order Lifecycle

Kofi manages a complex, state-driven pipeline designed for maximum transparency:

### The "Weighing" Pivot (Per-Kg Mode)
For orders set to **Per-Kg**, the process introduces a critical validation step:
1. **Initial Order**: Customer provides an `estimated_weight`. Kofi receives the order as `PENDING`.
2. **Pickup**: Driver collects items. Kofi updates status to `PICKED_UP`.
3. **The Weighing Event**: Upon arrival at the shop, Kofi weighs the load and enters the **`actual_weight`**.
4. **Final Pricing**: The system automatically calculates the `final_price` based on his `price_per_kg_snapshot`.
5. **Customer Confirmation**: Order moves to `WEIGHED`. If there's a significant delta, the customer is notified to pay the balance before processing begins.

### Status Progression & Audit Logs
Kofi tracks the "living" state of every order with dedicated timestamps and history logs:
- `PENDING` → `CONFIRMED` → `PICKED_UP` → `IN_PROCESS` → `OUT_FOR_DELIVERY` → `DELIVERED`.
- **Transparency**: Every status change generates a `TrackingLog` and an `OrderStatusHistory` entry, allowing Kofi to audit exactly which staff member moved an order and when.

### Edge Case Handling
- **Rejection & Cancellation**: If Kofi lacks capacity, he marks the order as `REJECTED` and must provide a `rejection_reason`.
- **Dual Address Support**: Kofi sees different addresses for "Pickup" and "Delivery" to handle customers who need laundry collected from work but delivered to home.

---

## 5. Customer Relationship Management (CRM)

### Loyalty & Retention Logic
Kofi builds a loyal customer base using the platform's native CRM features:
- **Loyalty Program**: Every `COMPLETED` order automatically awards the customer **Loyalty Points**. Kofi can see which customers are "High-Value" based on their points balance.
- **Engagement History**: Kofi accesses a customer's full order history, total lifetime spend, and address labels (Home, Work, etc.).
- **Feedback Loop**: He reviews **Ratings & Comments**. If a customer leaves a sub-4-star review, he gets an immediate notification to address the service gap.

---

## 6. Workforce & Logistics Management

Kofi optimizes his team’s efficiency through role-based assignment and real-time tracking:

### Specialized Staff Roles
Instead of a generic staff list, Kofi manages a team with granular responsibilities:
- **Receptionist**: Handles intake and initial order tagging.
- **Washer & Ironer**: Assigned to processing orders (Moves status to `IN_PROCESS`).
- **Driver**: Exclusively assigned to `DeliveryAssignment` tasks (Pickup or Delivery).
- **Invite Workflow**: Kofi sends invites via email. Only once a staff member accepts do they gain access to the shop's control panel.

### Logistic Assignments
Kofi oversees the field force with a bird's-eye view:
- **Driver Queue**: He assigns specific drivers to specific orders for pickup and delivery.
- **Real-Time Status**: Drivers mark assignments as `ASSIGNED` → `IN_TRANSIT` → `COMPLETED`, which instantly notifies the customer and updates Kofi’s dashboard.

---

## 7. Financial Ecosystem (The Payout Pivot)

Kofi maintains tight control over his shop’s liquidity and transaction history:

### Automated Revenue Splitting
Every order transaction processed via **Paystack** is subject to rules:
- **Platform Fees**: The system automatically deducts a pre-configured platform fee.
- **Taxes**: VAT and other taxes are calculated in the `FinanceService` and displayed in the order breakdown.
- **Coupons**: If a customer uses a promo code, the system validates eligibility based on Kofi's shop (e.g., first-time user, min order value).

### Bank Linkage & Payout Requests
Unlike a simple digital wallet, the platform uses a structured settlement system:
- **Linked Bank Accounts**: Kofi adds his business bank account, including his **Paystack Bank Code** for instant settlement.
- **Payout Trigger**: Kofi reviews his "Available Balance" (Revenue from `COMPLETED` orders minus previous payouts).
- **Audit Cycle**: He submits a **Payout Request**. This request follows a strict lifecycle: `PENDING` → `PROCESSING` → `COMPLETED`. Kofi can track every request in his "Payout History."

---

## 8. Proactive Notification Intelligence

## 8. Proactive Notification Intelligence

Kofi’s shop is automated by custom signals and background tasks:
- **Transactional Notifications**: Triggered by the `order_status_changed` signal, the system sends immediate updates to the `CUSTOMER` via the Connect app while Kofi’s dashboard flashes a real-time event.
- **Priority Alerts**: If a pickup slot is approaching, a Celery task generates an "Urgent" message to Kofi to ensure a driver is assigned.
- **Payment Verification**: Kofi receives a persistent notification the moment a **Paystack Webhook** confirms a payment, indicating he can safely move an order to `CONFIRMED`.

---

## 9. Security & Resilience

- **Idempotency**: The platform’s payment and status transition logic is designed to prevent "double-charging" or "duplicate statuses." Kofi can refresh his browser or lose internet connection without risking his data’s integrity.
- **Audit Trails**: Every order has an associated `OrderStatusHistory`. Kofi can click into any order to see an immutable timeline of who moved it and when (e.g., "Owner moved to IN_PROCESS," "Driver moved to OUT_FOR_DELIVERY").
- **Soft-Delete (Deactivation)**: If Kofi needs to temporarily step away, he can "Deactivate" his shop with a reason. This preserves his history while hiding him from the active marketplace.

---

## 10. Daily Workflow Scenario (Expert Edition)

### Morning Routine (08:00 AM)
Kofi logs into the **Command Center**. The dashboard shows 4 `PENDING` orders. He checks the **Machine List** – two washers are `IDLE`. He reviews the `pickup_date` and `service_type` for each order, then shifts them to `CONFIRMED`. He assigns his **Driver**, Emeka, to the morning pickup queue.

### Mid-Day Weighed Operations (12:00 PM)
Emeka returns with the first load. Kofi weighs the items and enters **5.5Kg** into the order detail. The system instantly calculates the `final_price` and sends a "Balance Due" notification to the customer. Once the **Paystack Webhook** marks the payment as `SUCCESS`, Kofi moves the load to `IN_PROCESS`.

### Afternoon Logistics (05:00 PM)
The shop's **Washer** staff marks items as ready. Kofi marks 5 orders as `OUT_FOR_DELIVERY`. He reviews the **Earnings Tab**, filtering for "Today." He sees a net revenue of N75,000, with platform commissions clearly deducted. He checks for new **Payout Requests** to ensure his cash flow stays fluid.

---

## 🎨 Expert Interface Breakdown

| Module | Core Logic & Features |
| :--- | :--- |
| **Authentication** | JWT Login, Secure Password Reset, UUID Integrity. |
| **Storefront** | GIS Map Picker, Opening Hours Manager, Machine State Toggle. |
| **Pricing Engine** | Per-Item vs Per-Kg Toggle, Price Snapshots, Delivery/Pickup Fees. |
| **Order Lifecycle** | State Machine Transitions, Weight-Entry, Audit Trail Logs. |
| **Finances** | Paystack Initialization, Coupon Validation, Payout Lifecycle. |
| **Logistics** | Driver Assignment, Pickup vs Delivery Address support, Tracking. |
| **CRM & Retention** | Loyalty Point Rewards, Customer Feedback reviews, Order History. |
| **Intelligence** | Real-time Dashboard KPIs, Celery-driven Priority Alerts. |

---

## 📋 Technical Checklist for Frontend Completion

- [ ] Implement **`actual_weight`** entry field for Per-Kg orders.
- [ ] Display **`OrderStatusHistory`** as a timeline in the Order Detail view.
- [ ] Build **`MachineManagement`** screen for inventory and status tracking.
- [ ] Implement **`PayoutRequest`** form with Bank Account selection.
- [ ] Ensure **`LoyaltyPoints`** are visible in the Customer Profile view within CRM.
