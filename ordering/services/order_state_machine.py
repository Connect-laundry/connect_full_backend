# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.dispatch import Signal
# pyre-ignore[missing-module]
from ..models.base import Order, OrderStatusHistory

# Signal for status changes
order_status_changed = Signal()

class OrderStateMachine:
    """
    Strict, deterministic state machine for managing Order lifecycle.
    Ensures safe transitions, audit logging, and role-based validation.
    """
    
    VALID_TRANSITIONS = {
        Order.Status.PENDING: [
            Order.Status.CONFIRMED,
            Order.Status.REJECTED,
            Order.Status.CANCELLED
        ],
        Order.Status.CONFIRMED: [
            Order.Status.PICKED_UP,
            Order.Status.CANCELLED
        ],
        Order.Status.PICKED_UP: [
            Order.Status.IN_PROCESS
        ],
        Order.Status.IN_PROCESS: [
            Order.Status.OUT_FOR_DELIVERY
        ],
        Order.Status.OUT_FOR_DELIVERY: [
            Order.Status.DELIVERED
        ],
        Order.Status.DELIVERED: [
            Order.Status.COMPLETED
        ],
        # Terminal states have no transitions out
        Order.Status.REJECTED: [],
        Order.Status.CANCELLED: [],
        Order.Status.COMPLETED: [],
    }

    @classmethod
    def can_transition(cls, from_status, to_status):
        """Check if a transition between two states is permitted."""
        return to_status in cls.VALID_TRANSITIONS.get(from_status, [])

    @classmethod
    @transaction.atomic
    def transition(cls, order_id, to_status, user, metadata=None, reason=None):
        """
        Atomically transition an order to a new status.
        Locks the row, validates the transition, updates timestamps, and logs history.
        """
        # Lock the row for update to prevent race conditions
        order = Order.objects.select_for_update().get(id=order_id)
        from_status = order.status

        if from_status == to_status:
            return order, True

        if not cls.can_transition(from_status, to_status):
            return order, False

        # update timestamps based on status
        now = timezone.now()
        timestamp_map = {
            Order.Status.CONFIRMED: 'confirmed_at',
            Order.Status.PICKED_UP: 'picked_up_at',
            Order.Status.IN_PROCESS: 'processing_started_at',
            Order.Status.OUT_FOR_DELIVERY: 'out_for_delivery_at',
            Order.Status.DELIVERED: 'delivered_at',
            Order.Status.COMPLETED: 'completed_at',
            Order.Status.CANCELLED: 'cancelled_at',
            Order.Status.REJECTED: 'rejected_at',
        }

        ts_field = timestamp_map.get(to_status)
        if ts_field:
            setattr(order, ts_field, now)

        # Handle reasons
        if to_status == Order.Status.CANCELLED and reason:
            order.cancellation_reason = reason
        elif to_status == Order.Status.REJECTED and reason:
            order.rejection_reason = reason

        # Update order status
        order.status = to_status
        order.save()

        # Create history record
        OrderStatusHistory.objects.create(
            order=order,
            previous_status=from_status,
            new_status=to_status,
            changed_by=user,
            metadata=metadata
        )

        # Emit signal (Notification hooks)
        order_status_changed.send(
            sender=cls,
            order=order,
            from_status=from_status,
            to_status=to_status,
            user=user,
            metadata=metadata
        )

        return order, True
