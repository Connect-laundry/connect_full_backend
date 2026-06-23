"""Builder for the customer-facing order tracking aggregator.

Keeps the heavy assembly out of `order_views.py` so the view stays thin.
Reuses existing services (FinanceService, OrderStateMachine) and the
OrderStatusHistory audit log — does not duplicate state.
"""
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from ordering.models.base import Order, OrderStatusHistory
from ordering.services.finance_service import FinanceService
from ordering.services.order_state_machine import OrderStateMachine


# The customer milestone timeline. Order matters — this is the visual order
# in the UI. Each milestone maps to one backend Order.Status. Statuses that
# are not part of the happy path (REJECTED, CANCELLED) are reported as
# branch_terminal entries in the activity log, not as timeline milestones.
TIMELINE_MILESTONES = [
    {
        "status": Order.Status.PENDING,
        "label": "Order placed",
        "description": "We've received your order and are waiting for the laundry to accept it.",
        "icon": "receipt",
    },
    {
        "status": Order.Status.CONFIRMED,
        "label": "Confirmed by laundry",
        "description": "The laundry has accepted your order and will pick it up soon.",
        "icon": "checkmark-circle",
    },
    {
        "status": Order.Status.PICKED_UP,
        "label": "Picked up",
        "description": "Your laundry has been collected and is on its way to the wash room.",
        "icon": "cube",
    },
    {
        "status": Order.Status.IN_PROCESS,
        "label": "In process",
        "description": "Your laundry is being washed, dried, and pressed.",
        "icon": "water",
    },
    {
        "status": Order.Status.OUT_FOR_DELIVERY,
        "label": "Out for delivery",
        "description": "Your fresh laundry is on its way back to you.",
        "icon": "bicycle",
    },
    {
        "status": Order.Status.DELIVERED,
        "label": "Delivered",
        "description": "Your laundry has been delivered. Enjoy!",
        "icon": "home",
    },
]

# Map each status → which order field stores its transition timestamp.
STATUS_TIMESTAMP_FIELDS = {
    Order.Status.PENDING: "created_at",
    Order.Status.CONFIRMED: "confirmed_at",
    Order.Status.PICKED_UP: "picked_up_at",
    Order.Status.IN_PROCESS: "processing_started_at",
    Order.Status.OUT_FOR_DELIVERY: "out_for_delivery_at",
    Order.Status.DELIVERED: "delivered_at",
    Order.Status.COMPLETED: "completed_at",
    Order.Status.CANCELLED: "cancelled_at",
    Order.Status.REJECTED: "rejected_at",
}

# Order in which the happy-path statuses are reached. Used to mark
# milestones as completed when an order has progressed past them.
HAPPY_PATH = [
    Order.Status.PENDING,
    Order.Status.CONFIRMED,
    Order.Status.PICKED_UP,
    Order.Status.IN_PROCESS,
    Order.Status.OUT_FOR_DELIVERY,
    Order.Status.DELIVERED,
    Order.Status.COMPLETED,
]

TERMINAL_STATUSES = {Order.Status.DELIVERED, Order.Status.COMPLETED, Order.Status.CANCELLED, Order.Status.REJECTED}


def derive_otp(order: Order) -> str:
    """Deterministic 4-digit code derived from the order id.

    Decorative until a driver app exists to verify it at the door; renders
    consistently across refreshes so it can be shown to the customer as their
    "show this to your courier" code.
    """
    digits = "".join(ch for ch in order.order_no if ch.isdigit())
    if len(digits) >= 4:
        return digits[-4:]
    # Fallback: hash the uuid hex to a 4-digit window.
    hex_str = order.id.hex
    value = int(hex_str[:8], 16) % 10000
    return f"{value:04d}"


def estimated_completion(order: Order) -> Optional[str]:
    """Best-effort projected completion timestamp (ISO string).

    Uses laundry.estimated_delivery_hours and either the confirmed_at
    timestamp (if confirmed) or the pickup_date as the anchor. Returns None
    for terminal/cancelled orders.
    """
    if order.status in (Order.Status.CANCELLED, Order.Status.REJECTED, Order.Status.COMPLETED, Order.Status.DELIVERED):
        return None

    laundry = order.laundry
    hours = getattr(laundry, "estimated_delivery_hours", None) or 24
    anchor = order.confirmed_at or order.pickup_date or order.created_at
    if anchor is None:
        return None
    eta = anchor + timedelta(hours=int(hours))
    return eta.isoformat()


def _serialize_iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _build_timeline(order: Order, history: list[OrderStatusHistory]) -> list[dict]:
    """Build the milestone list, marking each as completed / current / future.

    Decision rules:
      * Compute `reached_index`: the highest happy-path index the order ever
        attained. For happy-path statuses this is just the current status; for
        branch-terminal (CANCELLED/REJECTED) we read the last `previous_status`
        from the OrderStatusHistory, falling back to PENDING (0).
      * A milestone is **completed** if its index < reached_index, OR if the
        order is in a terminal happy-path state (DELIVERED/COMPLETED — every
        milestone is done), OR if the order is branch-terminal AND
        milestone_index <= reached_index (everything reached before the
        cancellation is locked complete).
      * A milestone is **current** only on happy-path orders that aren't
        terminal-complete, where milestone_index == reached_index.

    Timestamps are sourced from the order's own per-status fields where
    populated, otherwise from the OrderStatusHistory entry that recorded the
    transition into that status.
    """
    history_by_status = {entry.new_status: entry for entry in history}
    current_status = order.status
    is_branch_terminal = current_status in (Order.Status.REJECTED, Order.Status.CANCELLED)
    is_terminal_happy_path = current_status in (Order.Status.DELIVERED, Order.Status.COMPLETED)

    if is_branch_terminal:
        # Find the last happy-path status we were in before the branch.
        reached_index = 0  # PENDING — order must have existed to be cancelled
        for entry in history:
            prev = entry.previous_status
            if prev in HAPPY_PATH:
                reached_index = max(reached_index, HAPPY_PATH.index(prev))
    elif current_status in HAPPY_PATH:
        reached_index = HAPPY_PATH.index(current_status)
    else:
        reached_index = 0

    timeline = []
    for milestone in TIMELINE_MILESTONES:
        status = milestone["status"]
        order_field = STATUS_TIMESTAMP_FIELDS.get(status)
        order_ts = getattr(order, order_field, None) if order_field else None
        history_ts = history_by_status[status].timestamp if status in history_by_status else None
        timestamp = order_ts or history_ts
        milestone_index = HAPPY_PATH.index(status)

        if is_terminal_happy_path:
            is_completed = True
            is_current = False
        elif is_branch_terminal:
            is_completed = milestone_index <= reached_index
            is_current = False
        else:
            is_completed = milestone_index < reached_index
            is_current = milestone_index == reached_index

        timeline.append({
            "status": status,
            "label": milestone["label"],
            "description": milestone["description"],
            "icon": milestone["icon"],
            "timestamp": _serialize_iso(timestamp),
            "is_completed": is_completed,
            "is_current": is_current,
        })

    return timeline


def _build_items(order: Order) -> list[dict]:
    items = []
    for item in order.items.all():
        quantity = item.quantity or 0
        unit_price = item.price or Decimal("0.00")
        items.append({
            "id": str(item.id),
            "name": item.name,
            "service_type": item.service_type.name if item.service_type else None,
            "quantity": quantity,
            "unit_price": str(Decimal(str(unit_price)).quantize(Decimal("0.01"))),
            "subtotal": str((Decimal(str(unit_price)) * Decimal(quantity)).quantize(Decimal("0.01"))),
        })
    return items


def _build_charges_and_payment(order: Order) -> dict:
    breakdown = FinanceService.calculate_price_breakdown(order, coupon=order.coupon)
    total = Decimal(breakdown["total"])

    payment = getattr(order, "payment", None)
    paid_amount = Decimal("0.00")
    payment_status = "UNPAID"
    payment_method = None
    paid_at = None
    transaction_reference = None
    if payment is not None:
        payment_status = payment.status
        payment_method = payment.payment_method
        transaction_reference = payment.transaction_reference
        paid_at = _serialize_iso(payment.paid_at)
        if payment.status == "SUCCESS":
            paid_amount = Decimal(str(payment.amount or "0"))

    balance = (total - paid_amount).quantize(Decimal("0.01"))
    if balance < Decimal("0.00"):
        balance = Decimal("0.00")

    return {
        "charges": {
            "items_subtotal": breakdown["items_total"],
            "pickup_fee": breakdown["pickup_fee"],
            "delivery_fee": breakdown["delivery_fee"],
            "tax": breakdown["tax"],
            "platform_fee": breakdown["platform_fee"],
            "discount": breakdown["discount"],
            "total": breakdown["total"],
            "currency": breakdown["currency"],
        },
        "payment": {
            "status": payment_status,
            "order_payment_status": order.payment_status,
            "method": payment_method,
            "paid_amount": str(paid_amount.quantize(Decimal("0.01"))),
            "balance": str(balance),
            "paid_at": paid_at,
            "transaction_reference": transaction_reference,
        },
    }


def _build_laundry(order: Order, request=None) -> dict:
    laundry = order.laundry
    image_url = None
    if laundry.image:
        try:
            url = laundry.image.url
            image_url = request.build_absolute_uri(url) if request else url
        except Exception:
            image_url = None

    return {
        "id": str(laundry.id),
        "name": laundry.name,
        "address": laundry.address,
        "phone": laundry.phone_number,
        "rating": None,  # avg rating is computed on list queryset; deliberately omitted here
        "image_url": image_url,
        "estimated_delivery_hours": laundry.estimated_delivery_hours,
    }


def _build_activity(history: list[OrderStatusHistory]) -> list[dict]:
    log = []
    for entry in history:
        log.append({
            "id": str(entry.id),
            "previous_status": entry.previous_status,
            "new_status": entry.new_status,
            "timestamp": _serialize_iso(entry.timestamp),
            "actor": entry.changed_by.get_full_name() if entry.changed_by else "System",
            "metadata": entry.metadata or {},
        })
    return log


def build_tracking_payload(order: Order, request=None) -> dict:
    """Assemble the full tracking payload returned by /orders/{id}/tracking/."""
    history = list(order.status_history.all().order_by("timestamp").select_related("changed_by"))
    timeline = _build_timeline(order, history)
    charges_payment = _build_charges_and_payment(order)
    is_terminal = order.status in TERMINAL_STATUSES

    valid_next = list(OrderStateMachine.VALID_TRANSITIONS.get(order.status, []))
    customer_can_cancel = Order.Status.CANCELLED in valid_next

    return {
        "order": {
            "id": str(order.id),
            "order_no": order.order_no,
            "otp": derive_otp(order),
            "status": order.status,
            "status_label": order.get_status_display(),
            "is_terminal": is_terminal,
            "created_at": _serialize_iso(order.created_at),
            "pickup_date": _serialize_iso(order.pickup_date),
            "delivery_date": _serialize_iso(order.delivery_date),
            "estimated_completion": estimated_completion(order),
            "special_instructions": order.special_instructions or "",
            "pickup_address": order.pickup_address,
            "delivery_address": order.delivery_address,
            "cancellation_reason": order.cancellation_reason or None,
            "rejection_reason": order.rejection_reason or None,
        },
        "timeline": timeline,
        "items": _build_items(order),
        "charges": charges_payment["charges"],
        "payment": charges_payment["payment"],
        "laundry": _build_laundry(order, request=request),
        "activity": _build_activity(history),
        "can_cancel": customer_can_cancel,
    }
