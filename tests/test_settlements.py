"""
The platform's debts to laundries, and the frozen prices they are settled on.

Customers pay into the platform's account, so every successful payment creates
money the platform is holding on someone else's behalf. These tests cover the
two things that make that safe: prices that cannot move after the fact, and a
ledger that says who is owed what.
"""

from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone

from laundries.models.service import LaundryService
from ordering.models import Order
from ordering.services.finance_service import FinanceService
from payments.models import OrderSettlement, Payment, Payout
from payments.services.settlement_service import SettlementService

from test_payments import (
    _build_order,
    _build_pending_payment,
    _post_signed_webhook,
    _webhook_payload,
)
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestPriceSnapshot:
    def test_freezing_stores_every_component(self):
        _, order = _build_order()

        breakdown = FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        assert order.priced_at is not None
        assert order.items_total == Decimal('25.00')
        assert order.total_amount == Decimal(breakdown['total'])
        assert order.currency == 'GHS'

    def test_a_later_fee_change_does_not_rewrite_history(self):
        # Line-item prices were already snapshotted onto OrderItem. The fees
        # were not: they were re-read from the laundry profile on every request,
        # so raising a delivery fee silently restated every past order.
        _, order = _build_order()
        order.laundry.delivery_fee = Decimal('5.00')
        order.laundry.save(update_fields=['delivery_fee'])

        with override_settings(DELIVERY_FEES_IN_APP=True):
            FinanceService.freeze_price_breakdown(order)
            order.refresh_from_db()
            original = FinanceService.calculate_price_breakdown(order)
            assert original['delivery_fee'] == '5.00'

            # The laundry triples its delivery fee the next day.
            order.laundry.delivery_fee = Decimal('15.00')
            order.laundry.save(update_fields=['delivery_fee'])
            order.refresh_from_db()

            assert FinanceService.calculate_price_breakdown(order) == original

    def test_a_later_rate_change_does_not_rewrite_history(self):
        _, order = _build_order()
        with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
            FinanceService.freeze_price_breakdown(order)
            order.refresh_from_db()
            original = FinanceService.calculate_price_breakdown(order)

        assert original['platform_fee'] == '0.00'
        assert original['total'] == '25.00'

        # Commission and VAT are introduced later. Orders already placed must
        # not retroactively acquire charges the customer never paid.
        with override_settings(PLATFORM_FEE_RATE=0.15, TAX_RATE=0.20):
            assert FinanceService.calculate_price_breakdown(order) == original

    def test_line_item_prices_are_snapshotted_on_the_order_item(self):
        # This has always held: OrderItem stores its own price.
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)

        LaundryService.objects.filter(laundry=order.laundry).update(price=Decimal('50.00'))
        order.refresh_from_db()

        assert FinanceService.calculate_price_breakdown(order)['items_total'] == '25.00'

    @override_settings(DELIVERY_FEES_IN_APP=False)
    def test_the_snapshot_records_how_logistics_were_settled(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        assert order.delivery_fees_in_app is False
        assert FinanceService.calculate_price_breakdown(order)['delivery_fees_in_app'] is False

    def test_orders_without_a_snapshot_still_compute_live(self):
        # Orders placed before snapshots existed have no priced_at. They must
        # keep working rather than reporting zeroes.
        _, order = _build_order()
        assert order.priced_at is None

        breakdown = FinanceService.calculate_price_breakdown(order)
        assert breakdown['items_total'] == '25.00'


@pytest.mark.django_db
class TestSettlementRecording:
    def test_a_paid_order_creates_a_debt_to_the_laundry(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order)

        assert settlement.laundry_id == order.laundry_id
        assert settlement.gross_amount == order.total_amount
        assert settlement.status == OrderSettlement.Status.HELD

    def test_with_no_commission_the_laundry_is_owed_everything(self):
        _, order = _build_order()
        with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
            FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order)

        assert settlement.platform_commission == Decimal('0.00')
        assert settlement.net_payable == settlement.gross_amount

    def test_commission_is_deducted_when_one_is_charged(self):
        _, order = _build_order()
        with override_settings(PLATFORM_FEE_RATE=0.10, TAX_RATE=0.00):
            FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order)

        assert settlement.platform_commission == Decimal('2.50')
        assert settlement.net_payable == settlement.gross_amount - Decimal('2.50')

    def test_recording_twice_does_not_double_credit_the_laundry(self):
        # Paystack retries webhooks. A repeat must not create a second debt.
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        first = SettlementService.record_for_order(order)
        second = SettlementService.record_for_order(order)

        assert first.id == second.id
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_the_processor_fee_is_recorded_but_not_deducted(self):
        # Who absorbs Paystack's cut is a commercial decision, so it is stored
        # for visibility rather than silently taken off the laundry's money.
        _, order = _build_order()
        with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
            FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order, processor_fee=Decimal('0.49'))

        assert settlement.processor_fee == Decimal('0.49')
        assert settlement.net_payable == settlement.gross_amount


@pytest.mark.django_db
class TestSettlementReversal:
    def test_a_refund_cancels_the_debt(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()
        SettlementService.record_for_order(order)

        settlement = SettlementService.reverse_for_order(order, reason='Customer refunded')

        assert settlement.status == OrderSettlement.Status.REVERSED
        assert settlement.reversed_at is not None
        assert settlement.reversal_reason == 'Customer refunded'

    def test_reversing_is_idempotent(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()
        SettlementService.record_for_order(order)

        first = SettlementService.reverse_for_order(order)
        reversed_at = first.reversed_at
        second = SettlementService.reverse_for_order(order)

        assert second.reversed_at == reversed_at

    def test_a_replayed_payment_webhook_cannot_resurrect_a_reversed_debt(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()
        SettlementService.record_for_order(order)
        SettlementService.reverse_for_order(order)

        SettlementService.record_for_order(order)

        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.REVERSED

    def test_reversing_a_missing_settlement_is_harmless(self):
        _, order = _build_order()
        assert SettlementService.reverse_for_order(order) is None


@pytest.mark.django_db
class TestEscrow:
    """Money is held until the customer has their clothes back."""

    def _paid_order(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()
        SettlementService.record_for_order(order)
        return order

    def test_payment_alone_does_not_make_money_payable(self):
        # Otherwise a laundry could be paid for clothes it never collected.
        order = self._paid_order()

        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')
        assert SettlementService.held_total(order.laundry) > Decimal('0.00')

    def test_a_held_settlement_cannot_be_swept_into_a_payout(self):
        order = self._paid_order()
        assert SettlementService.build_payout(order.laundry) is None

    def test_delivery_releases_the_money(self):
        order = self._paid_order()

        SettlementService.release_for_order(order)

        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.PENDING
        assert SettlementService.held_total(order.laundry) == Decimal('0.00')
        assert SettlementService.outstanding_total(order.laundry) == settlement.net_payable

    def test_releasing_twice_is_harmless(self):
        order = self._paid_order()
        SettlementService.release_for_order(order)

        assert SettlementService.release_for_order(order) is None

    def test_a_reversed_settlement_is_never_released(self):
        # A refunded order must not become payable because the laundry later
        # marked it delivered.
        order = self._paid_order()
        SettlementService.reverse_for_order(order)

        assert SettlementService.release_for_order(order) is None
        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.REVERSED

    def test_releasing_an_order_with_no_settlement_is_harmless(self):
        _, order = _build_order()
        assert SettlementService.release_for_order(order) is None

    def test_the_earnings_summary_separates_held_from_available(self):
        order = self._paid_order()

        summary = SettlementService.earnings_summary(order.laundry)
        assert summary['available'] == '0.00'
        assert Decimal(summary['held']) > Decimal('0.00')

        SettlementService.release_for_order(order)

        summary = SettlementService.earnings_summary(order.laundry)
        assert summary['held'] == '0.00'
        assert Decimal(summary['available']) > Decimal('0.00')


@pytest.mark.django_db
class TestOutstandingAndPayouts:
    def _released_order(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()
        SettlementService.record_for_order(order)
        SettlementService.release_for_order(order)
        return order

    def test_outstanding_sums_only_released_debts(self):
        order = self._released_order()
        settlement = OrderSettlement.objects.get(order=order)

        assert SettlementService.outstanding_total(order.laundry) == settlement.net_payable

        SettlementService.reverse_for_order(order)
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_outstanding_is_zero_for_a_laundry_owed_nothing(self):
        _, order = _build_order()
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_building_a_payout_claims_the_released_debts(self):
        order = self._released_order()
        settlement = OrderSettlement.objects.get(order=order)

        payout = SettlementService.build_payout(order.laundry)

        settlement.refresh_from_db()
        assert payout.amount == settlement.net_payable
        assert settlement.status == OrderSettlement.Status.SCHEDULED
        assert settlement.payout_id == payout.id
        # Claimed debts leave the outstanding pool so they cannot be paid twice.
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_building_a_payout_with_nothing_owed_returns_nothing(self):
        _, order = _build_order()
        assert SettlementService.build_payout(order.laundry) is None

    def test_marking_a_payout_paid_settles_its_debts(self):
        order = self._released_order()
        settlement = OrderSettlement.objects.get(order=order)
        payout = SettlementService.build_payout(order.laundry)

        SettlementService.mark_payout_paid(payout, reference='TRF-001')

        payout.refresh_from_db()
        settlement.refresh_from_db()
        assert payout.status == Payout.Status.PAID
        assert payout.reference == 'TRF-001'
        assert payout.paid_at is not None
        assert settlement.status == OrderSettlement.Status.PAID

    def test_a_refunded_order_is_not_paid_out_with_its_batch(self):
        # It was swept into the payout before the refund landed. Marking the
        # batch paid must not quietly mark the reversed debt as paid too.
        order = self._released_order()
        settlement = OrderSettlement.objects.get(order=order)
        payout = SettlementService.build_payout(order.laundry)
        SettlementService.reverse_for_order(order)

        SettlementService.mark_payout_paid(payout)

        settlement.refresh_from_db()
        assert settlement.status == OrderSettlement.Status.REVERSED


@pytest.mark.django_db
class TestWebhookCreatesSettlement:
    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_a_successful_payment_webhook_records_the_debt(self):
        _, order, payment = _build_pending_payment(reference='ORD-SETTLE-1')
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        client = APIClient()
        payload = _webhook_payload(payment, event_id='evt_settle_1')
        payload['data']['fees'] = 49  # pesewas

        response = _post_signed_webhook(client, payload)

        assert response.status_code == 200
        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.HELD
        assert settlement.net_payable == order.total_amount
        assert settlement.processor_fee == Decimal('0.49')

    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_money_is_held_at_payment_and_released_at_delivery(self):
        """The full escrow lifecycle, driven through the real state machine."""
        from ordering.services.order_state_machine import OrderStateMachine

        _, order, payment = _build_pending_payment(reference='ORD-ESCROW-1')
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        client = APIClient()
        _post_signed_webhook(client, _webhook_payload(payment, event_id='evt_escrow_1'))

        # Paid, but the laundry has not touched the clothes yet.
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')
        assert SettlementService.held_total(order.laundry) == order.total_amount

        for status in (
            Order.Status.PICKED_UP,
            Order.Status.IN_PROCESS,
            Order.Status.OUT_FOR_DELIVERY,
        ):
            OrderStateMachine.transition(order.id, status, user=None)
            assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

        # Delivery proved with the customer's handover code releases at once.
        order.refresh_from_db()
        order.delivery_confirmed_by_code = True
        order.save(update_fields=['delivery_confirmed_by_code'])
        OrderStateMachine.transition(order.id, Order.Status.DELIVERED, user=None)

        assert SettlementService.held_total(order.laundry) == Decimal('0.00')
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_a_replayed_webhook_does_not_double_credit(self):
        _, order, payment = _build_pending_payment(reference='ORD-SETTLE-2')
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        client = APIClient()
        payload = _webhook_payload(payment, event_id='evt_settle_2')
        _post_signed_webhook(client, payload)
        _post_signed_webhook(client, payload)

        assert OrderSettlement.objects.filter(order=order).count() == 1
