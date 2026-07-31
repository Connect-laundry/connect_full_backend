"""
Where a customer's payment lands.

Two routes exist, and every order takes exactly one of them:

* **Platform-collected.** Money arrives in the platform's Paystack account and
  an ``OrderSettlement`` records what the laundry is owed. Someone pays it out
  later. This is the default and the fallback.
* **Direct settlement.** Money is routed to the laundry's own Paystack
  subaccount at charge time. Nothing is owed afterwards because nothing was
  held.

Direct settlement is off unless three things are true at once: the global
switch is on, the laundry has a subaccount code, and that laundry has been
individually enabled. The narrow gate is deliberate — once money is split out
it cannot be held back if the order goes wrong, so a laundry earns this rather
than getting it by default.
"""

import logging
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)


class SplitRoute:
    """How one order's payment should be routed."""

    def __init__(self, subaccount_code='', platform_charge_pesewas=0, bearer='account'):
        self.subaccount_code = subaccount_code
        self.platform_charge_pesewas = platform_charge_pesewas
        self.bearer = bearer

    @property
    def is_direct(self):
        return bool(self.subaccount_code)

    def __repr__(self):  # pragma: no cover - debugging aid
        return (
            f"SplitRoute(direct={self.is_direct}, "
            f"charge={self.platform_charge_pesewas}, bearer={self.bearer})"
        )


def split_payments_enabled():
    """Global kill switch. Off until a real transaction has been verified."""
    return bool(getattr(settings, 'PAYSTACK_SPLIT_ENABLED', False))


def resolve_route(order):
    """
    Decide how this order's payment is routed.

    Returns a platform-collected route (no subaccount) whenever anything is
    missing. Failing towards the platform account is the safe direction: the
    money is merely held rather than sent somewhere unintended, and the
    settlement ledger still records who it belongs to.
    """
    if not split_payments_enabled():
        return SplitRoute()

    laundry = getattr(order, 'laundry', None)
    if laundry is None:
        return SplitRoute()

    if not getattr(laundry, 'split_payments_enabled', False):
        return SplitRoute()

    code = (getattr(laundry, 'paystack_subaccount_code', '') or '').strip()
    if not code:
        logger.warning(
            "Laundry marked for direct settlement has no subaccount code",
            extra={"laundry_id": str(getattr(laundry, 'id', ''))},
        )
        return SplitRoute()

    return SplitRoute(
        subaccount_code=code,
        platform_charge_pesewas=platform_charge_pesewas(order),
        bearer=getattr(settings, 'PAYSTACK_SPLIT_BEARER', 'account'),
    )


def platform_charge_pesewas(order):
    """
    The platform's cut of this order, in pesewas, for Paystack's
    ``transaction_charge``.

    Taken from the order's frozen snapshot rather than recomputed, so the
    amount split out matches the amount the customer was shown. Zero while the
    app is free to use, which sends the laundry the whole payment.
    """
    commission = getattr(order, 'platform_fee', None)
    if commission is None:
        return 0
    try:
        pesewas = (Decimal(str(commission)) * 100).quantize(Decimal('1'))
    except (TypeError, ValueError, ArithmeticError):
        return 0
    return max(0, int(pesewas))
