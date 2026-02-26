# User Story: Connect Laundry Owner Web Application

## Persona

- **Name**: Kofi Mensah
- **Role**: Laundry Business Owner
- **Business**: "Kofi’s Premium Cleaners" (Mid-sized neighborhood service)
- **Tech Proficiency**: Basic to Moderate
- **Primary Device**: Desktop/Laptop Browser

---

## 1. Account Creation & Onboarding

### Registration & Verification

Kofi visits the Connect Laundry portal. He selects "Register as Vendor." He enters his business email, phone number, and name. He sets a secure password.
Upon submission, he is redirected to a business profile wizard. He must upload his business registration document for platform verification (Admin vetting).

### Secure Login

Kofi logs in using his credentials. The system uses JWT (JSON Web Tokens) for authentication, eliminating the need for complex OTPs. His `OWNER` role is verified instantly, granting him access to the management suite.

### First-time Walkthrough

The first login triggers a guided tour highlighting the Quick-Stats panel, the Order Queue, and the Service Management tab.

---

## 2. Dashboard Overview

### Real-Time Metrics

The dashboard serves as Kofi's command center. He sees:

- **Total Orders**: Lifetime count.
- **Pending Orders**: Orders awaiting acceptance.
- **Completed Orders**: Orders delivered and paid.
- **Revenue Today**: Sum of earnings from successful deliveries today.
- **Revenue This Month**: Aggregated monthly earnings.
- **Active Customers**: Count of unique customers with recent orders.

### Visual Analytics

- **Order Volume Chart**: A 7-day bar chart showing the daily distribution of laundry loads.
- **Revenue Trend**: A line graph comparing current month performance against the previous month.

### Notification Center

A sidebar or bell icon alerts Kofi to new orders, payment confirmations, or negative reviews requiring immediate attention.

---

## 3. Business Profile Setup

Kofi configures his digital storefront to attract customers:

- **Branding**: Uploads the "Kofi’s Premium Cleaners" logo and a high-quality cover photo of the shop.
- **Availability**: Toggles the "Open" switch. If he goes on vacation, he can toggle "Closed" to hide his shop from the customer app.
- **Operating Hours**: Sets specific intervals for Monday–Sunday (e.g., 8:00 AM – 6:00 PM).
- **Service Catalog**:
  - Adds items (e.g., "Regular Shirt", "Designer Suit", "Duvet").
  - Sets prices for **Wash**, **Iron**, and **Dry Clean**.
  - Toggles "Active" status for specific services based on equipment availability.
- **Logistics**: Sets his **Delivery Fee** and **Minimum Order Value** (e.g., N5,000 min order, N1,000 delivery).

---

## 4. Order Management Flow

### Receiving & Vetting

Kofi receives a browser notification for a new order. On the **Order Detail Screen**, he reviews:

- Customer name and location.
- List of items (e.g., 5 shirts, 2 trousers).
- Selected service type (Wash & Iron).
- Special instructions ("Handle with care, use specific detergent").
- Scheduled pickup and delivery slots.

### Operational Lifecycle

Kofi processes the order through the pipeline:

1. **Accept/Reject**: Checks capacity and accepts the order. Status moves to `CONFIRMED`.
2. **Staff Assignment**: Kofi assigns a staff member or driver for the pickup.
3. **Status Updates**:
   - **Picked Up**: Notification sent to customer when staff collects items.
   - **In Progress (Washing/Ironing)**: Items are being processed in-house.
   - **Ready for Delivery**: Items are packaged and tagged.
   - **Out for Delivery**: Driver is en route to the customer.
   - **Completed**: Items delivered. Payment status is finalized.

### Edge Cases

- **Cancelled Orders**: If a customer cancels before pickup, Kofi receives an alert.
- **Notes & Logs**: Kofi adds an internal note: "Item arrived with slight fraying on collar - informed customer."

---

## 5. Customer Management

### CRM View

Kofi accesses the **Customer List** to build loyalty:

- **Search**: Quickly finds a customer by name or phone number.
- **History**: Views a customer’s lifetime order count and total spend.
- **Blacklisting**: Blocks "problematic" customers who repeatedly cancel or provide fraudulent payment cues.

---

## 6. Staff & Driver Management

### Team Control

Kofi manages his small team:

- **Add Staff**: Registers workers by email and assigns roles (In-shop laundry staff or Field Driver).
- **Assignment**: Directly assigns orders to drivers for pickup/delivery.
- **Activity Tracking**: Sees which driver is currently "In-Transit" with which order.

---

## 7. Financial Management

### Revenue & Reporting

Kofi monitors his cash flow:

- **Earnings Tab**: Filters revenue by "This Week," "This Month," or custom date ranges.
- **Export**: Generates a CSV report for bookkeeping or tax purposes.
- **Platform Fees**: Clear view of the gross amount vs. the platform's commission and his net payout.

---

## 8. Notifications & Alerts

### System Intelligence

- **High Priority**: "Urgent: Order #123 pickup slot starts in 15 minutes."
- **Transactional**: "Payment of N12,500 verified for Order #123."
- **Customer Feedback**: "New Review: 5 stars - Great service!"

---

## 9. Settings & Security

### Account Safety

Kofi can update his personal profile, change his password, and review active login sessions. He can also update business banking details for payouts.

---

## 10. Daily Workflow Scenario (A Day in the Life)

### Morning Routine (08:00 AM)

Kofi arrives at the shop and logs into the Web App. He checks the **Dashboard** for orders placed overnight. He sees 3 `PENDING` orders. He reviews their locations and accepts all three. He assigns his driver, Emeka, to pick up the items during his morning route.

### Mid-Day Operations (12:00 PM)

The app notifies him that the morning orders are now `PICKED_UP`. He moves them to `IN_PROCESS` as the laundry team starts washing. He receives a new order for "Express Dry Cleaning" and prioritizes it in the queue.

### Afternoon & Closing (05:00 PM)

Kofi reviews the "Ready for Delivery" list. He marks 5 orders as `OUT_FOR_DELIVERY`. At 6:00 PM, he checks the **Earnings** tab to see today's revenue. He sees total sales of N55,000. He logs out, knowing the system has automatically notified customers of their successful deliveries.

---

## 🎨 Suggested Frontend Screens

1. **Login Page**: Clean, minimalist JWT-based entry.
2. **Dashboard**: Grid layout with big metric cards and a centralized "Active Orders" table.
3. **Order Queue**: List view with status-colored badges (Yellow for Pending, Green for Completed).
4. **Order Detail View**: Modal or sidebar showing item breakdown and lifecycle transition buttons.
5. **Business Profile**: Form-heavy page for hours, pricing, and logo uploads.
6. **Earnings Page**: Data table with date filters and "Export" button.
7. **Staff List**: Simple cards showing staff names, roles, and current assignments.
8. **Settings**: Tabbed interface for account/security/notifications.

---

## 📋 Core Feature List

| Page               | Core Features                                                    |
| :----------------- | :--------------------------------------------------------------- |
| **Authentication** | Login, Password Reset, Registration Wizard.                      |
| **Dashboard**      | Revenue Cards, Order Volume Chart, Quick Notifications.          |
| **Orders**         | Status Filtering, Detail View, Staff Assignment, Status Toggles. |
| **Profile**        | Opening Hours Manager, Logo Upload, Address/Map Picker.          |
| **Services**       | Add/Edit Items, Price Management, Availability Toggle.           |
| **Finance**        | Transaction History, Date Filtering, CSV Export.                 |
| **Staff**          | Role Assignment, Driver Status Tracking.                         |
