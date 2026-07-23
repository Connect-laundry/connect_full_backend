# Connect Laundry Product Surface Scope

Audit decision date: 2026-05-20

## Customer Mobile App

`connect-customer-mobile` is the customer-facing mobile app. It should implement customer flows only:

- browse laundries
- manage favorites
- create customer orders
- complete or retry customer payments
- view customer order receipts and lifecycle status
- manage customer profile, addresses, sessions, notifications, referrals, and support

The app must not expose owner, admin, or driver controls through hidden mobile routes unless those product surfaces are explicitly designed and permission-tested.

## Non-Customer Surfaces

The Django backend already exposes operational capabilities for:

- laundry owner dashboard stats, earnings, orders, and service availability
- admin laundry and service approval
- delivery assignment management
- driver-visible assignments and lifecycle transitions
- admin monitoring hooks

These are **out of scope for the customer mobile app** and require separate product surfaces:

- owner web dashboard or owner mobile app
- admin web console beyond Django admin
- driver mobile app or driver mode

Until those surfaces exist, customer mobile must not imply that owner/admin/driver operations can be completed from this app.

