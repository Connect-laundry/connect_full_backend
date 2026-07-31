"""
The code a customer reads out when their clothes come back.

The platform has no riders and no independent observer of a delivery: a laundry
marks its own order delivered, and money is released on that word. The handover
code is the cheapest possible check on it. The customer sees a four-digit code
in their app; the laundry can only close the order with proof by asking for it.

Modelled on DoorDash's delivery PIN, including the part people skip: there is a
documented path for when the code cannot be collected, because customers are
unreachable often enough that blocking on it would strand real orders. An
uncoded delivery still closes. Its money simply waits out a dispute window
first, rather than paying instantly on an unverified claim.
"""

import secrets

from django.utils import timezone

CODE_LENGTH = 4


def generate_handover_code():
    """A four-digit code, uniform across the whole range including leading zeros."""
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def ensure_handover_code(order):
    """
    Give an order a handover code if it has none.

    Called when an order is confirmed, so the customer can see the code for the
    whole time their clothes are away rather than being asked for something
    that appears at the last moment.
    """
    if order.handover_code:
        return order.handover_code

    order.handover_code = generate_handover_code()
    order.save(update_fields=['handover_code', 'updated_at'])
    return order.handover_code


def verify_handover_code(order, submitted):
    """
    Check a code the laundry typed against the order's own.

    Compared in constant time and length-safe. This guards money, and a
    four-digit space is small enough that timing leaks are worth closing even
    though the practical risk is low.
    """
    expected = (order.handover_code or '').strip()
    provided = (str(submitted or '')).strip()
    if not expected or not provided:
        return False
    return secrets.compare_digest(expected, provided)


def mark_confirmed_by_code(order):
    """Record that this delivery was proved, so the escrow releases at once."""
    order.delivery_confirmed_by_code = True
    order.delivered_at = order.delivered_at or timezone.now()
    order.save(update_fields=['delivery_confirmed_by_code', 'delivered_at', 'updated_at'])
    return order
