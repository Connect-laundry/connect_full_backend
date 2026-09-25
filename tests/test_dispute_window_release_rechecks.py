"""
The dispute-window timer must re-check that the money is still owed before
it releases a HELD settlement.

A refund only reverses the settlement once Paystack reports it settled
(`refund.mark_refund_settled`). Between the staff refund request and that
webhook the payment sits in REFUND_PENDING while the settlement is still
HELD with its timer running. The staff refund is the documented response
to a disputed order (docs/production/PAYMENT_INCIDENT.md), so without a
re-check the timer could pay the laundry for an order whose customer is
being refunded.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone

from ordering.models import Order
from ordering.services.finance_service import FinanceService
from payments.models import OrderSettlement, Payment
from payments.services.settlement_service import SettlementService

from test_payments import _build_order


def _unproved_delivery(payment_status=Payment.Status.SUCCESS):
    customer, order = _build_order()
    with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
        FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    order.payment_status = Order.PaymentStatus.PAID
    order.status = Order.Status.DELIVERED
    order.save()
    Payment.objects.create(
        user=customer, order=order, amount=order.total_amount, currency='GHS',
        transaction_reference=f'DISPUTE-{order.id.hex[:8]}',
        payment_method=Payment.Method.CARD, status=payment_status,
    )
    SettlementService.record_for_order(order)
    SettlementService.release_for_order(order, confirmed=False)
    return order


def _after_window():
    return timezone.now() + timedelta(hours=49)


@pytest.mark.django_db
class TestDisputeWindowReleaseRechecks:
    @pytest.fixture(autouse=True)
    def _window(self, settings):
        settings.SETTLEMENT_AUTO_RELEASE_HOURS = 48

    def test_clean_order_releases_exactly_once_after_the_window(self):
        order = _unproved_delivery()

        assert SettlementService.run_auto_release(now=_after_window()) == 1
        assert SettlementService.run_auto_release(now=_after_window()) == 0

        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.PENDING
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_refund_in_flight_blocks_release(self):
        order = _unproved_delivery(payment_status=Payment.Status.REFUND_PENDING)

        assert SettlementService.run_auto_release(now=_after_window()) == 0

        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.HELD
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_refunded_payment_blocks_release(self):
        order = _unproved_delivery(payment_status=Payment.Status.REFUNDED)

        assert SettlementService.run_auto_release(now=_after_window()) == 0
        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.HELD

    def test_cancelled_order_blocks_release(self):
        order = _unproved_delivery()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)

        assert SettlementService.run_auto_release(now=_after_window()) == 0
        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.HELD

    def test_settlement_pointing_at_another_laundry_blocks_release(self):
        order = _unproved_delivery()
        other = _build_second_laundry(order)
        OrderSettlement.objects.filter(order=order).update(laundry=other)

        assert SettlementService.run_auto_release(now=_after_window()) == 0
        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.HELD

    def test_customer_confirmation_is_also_blocked_while_a_refund_is_in_flight(self):
        # The customer-confirm and code paths release through the same
        # release_for_order, so the re-check has to live there too.
        order = _unproved_delivery(payment_status=Payment.Status.REFUND_PENDING)

        SettlementService.release_for_order(order, confirmed=True)

        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.HELD
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')


def _build_second_laundry(order):
    from laundries.models.laundry import Laundry
    from users.models import User
    owner = User.objects.create_user(
        email='dispute-other-owner@example.com', phone='233555990177',
        password='StrongPass123!', role=User.Role.OWNER,
    )
    return Laundry.objects.create(
        name='Other Laundry', description='x', address='Accra', city='Accra',
        latitude='5.6', longitude='-0.18', phone_number='0240000177', owner=owner,
        status=Laundry.ApprovalStatus.APPROVED, is_active=True,
    )
