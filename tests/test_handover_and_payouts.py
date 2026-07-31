"""
Proving a delivery happened, and paying laundries without anyone pressing a
button.

Two independent problems. The handover code answers "did the customer really
get their clothes?", which matters because a laundry marks its own orders
delivered. The scheduled payout run answers "who is owed what today?", which
matters because nobody is going to open the admin every morning.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone

from ordering.models import Order
from ordering.services.finance_service import FinanceService
from ordering.services.handover import (
    ensure_handover_code,
    generate_handover_code,
    verify_handover_code,
)
from ordering.services.order_state_machine import OrderStateMachine
from payments.models import OrderSettlement, Payout
from payments.services.settlement_service import SettlementService

from test_payments import _build_order


def _paid_order():
    _, order = _build_order()
    with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
        FinanceService.freeze_price_breakdown(order)
    order.refresh_from_db()
    SettlementService.record_for_order(order)
    return order


class TestHandoverCode:
    def test_a_code_is_four_digits(self):
        for _ in range(50):
            code = generate_handover_code()
            assert len(code) == 4
            assert code.isdigit()

    def test_codes_with_leading_zeros_are_possible(self):
        # A generator that skips them quietly shrinks the space by a tenth.
        codes = {generate_handover_code() for _ in range(4000)}
        assert any(code.startswith('0') for code in codes)

    def test_codes_are_not_all_the_same(self):
        assert len({generate_handover_code() for _ in range(50)}) > 1


@pytest.mark.django_db
class TestHandoverVerification:
    def test_a_matching_code_verifies(self):
        _, order = _build_order()
        code = ensure_handover_code(order)
        assert verify_handover_code(order, code) is True

    def test_whitespace_around_the_code_is_forgiven(self):
        # The laundry owner is typing this on a phone with wet hands.
        _, order = _build_order()
        code = ensure_handover_code(order)
        assert verify_handover_code(order, f'  {code} ') is True

    def test_a_wrong_code_fails(self):
        _, order = _build_order()
        ensure_handover_code(order)
        wrong = '0000' if order.handover_code != '0000' else '1111'
        assert verify_handover_code(order, wrong) is False

    def test_an_empty_submission_fails(self):
        _, order = _build_order()
        ensure_handover_code(order)
        assert verify_handover_code(order, '') is False
        assert verify_handover_code(order, None) is False

    def test_an_order_with_no_code_cannot_be_verified(self):
        # Otherwise a blank submission would match a blank code.
        _, order = _build_order()
        assert order.handover_code == ''
        assert verify_handover_code(order, '') is False
        assert verify_handover_code(order, '1234') is False

    def test_a_code_is_issued_once_and_kept(self):
        _, order = _build_order()
        first = ensure_handover_code(order)
        assert ensure_handover_code(order) == first


@pytest.mark.django_db
class TestEscrowReleaseDependsOnProof:
    def test_a_proved_delivery_releases_immediately(self):
        order = _paid_order()
        order.delivery_confirmed_by_code = True
        order.save(update_fields=['delivery_confirmed_by_code'])

        SettlementService.release_for_order(order, confirmed=True)

        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.PENDING
        assert settlement.release_after is None

    @override_settings(SETTLEMENT_AUTO_RELEASE_HOURS=48)
    def test_an_unproved_delivery_waits_out_a_dispute_window(self):
        order = _paid_order()

        SettlementService.release_for_order(order, confirmed=False)

        settlement = OrderSettlement.objects.get(order=order)
        assert settlement.status == OrderSettlement.Status.HELD
        assert settlement.release_after is not None
        # Still not payable today.
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    @override_settings(SETTLEMENT_AUTO_RELEASE_HOURS=48)
    def test_the_window_expiring_releases_the_money(self):
        # A laundry that could not reach the customer must still get paid.
        order = _paid_order()
        SettlementService.release_for_order(order, confirmed=False)

        released = SettlementService.run_auto_release(
            now=timezone.now() + timedelta(hours=49)
        )

        assert released == 1
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    @override_settings(SETTLEMENT_AUTO_RELEASE_HOURS=48)
    def test_the_window_is_respected_before_it_expires(self):
        order = _paid_order()
        SettlementService.release_for_order(order, confirmed=False)

        assert SettlementService.run_auto_release(
            now=timezone.now() + timedelta(hours=1)
        ) == 0

    def test_auto_release_ignores_settlements_with_no_timer(self):
        # Held-but-undelivered money has no release_after and must never be
        # swept up by the timer job.
        order = _paid_order()
        assert SettlementService.run_auto_release() == 0
        assert OrderSettlement.objects.get(order=order).status == OrderSettlement.Status.HELD


@pytest.mark.django_db
class TestDeliveryThroughTheStateMachine:
    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_confirming_an_order_issues_a_handover_code(self):
        _, order = _build_order()
        OrderStateMachine.transition(order.id, Order.Status.CONFIRMED, user=None)

        order.refresh_from_db()
        assert len(order.handover_code) == 4

    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_an_unproved_delivery_does_not_pay_the_laundry_yet(self):
        order = _paid_order()
        for status in (
            Order.Status.CONFIRMED,
            Order.Status.PICKED_UP,
            Order.Status.IN_PROCESS,
            Order.Status.OUT_FOR_DELIVERY,
            Order.Status.DELIVERED,
        ):
            OrderStateMachine.transition(order.id, status, user=None)

        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')
        assert OrderSettlement.objects.get(order=order).release_after is not None

    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_a_proved_delivery_pays_the_laundry_at_once(self):
        order = _paid_order()
        for status in (
            Order.Status.CONFIRMED,
            Order.Status.PICKED_UP,
            Order.Status.IN_PROCESS,
            Order.Status.OUT_FOR_DELIVERY,
        ):
            OrderStateMachine.transition(order.id, status, user=None)

        order.refresh_from_db()
        order.delivery_confirmed_by_code = True
        order.save(update_fields=['delivery_confirmed_by_code'])
        OrderStateMachine.transition(order.id, Order.Status.DELIVERED, user=None)

        assert SettlementService.outstanding_total(order.laundry) == order.total_amount


@pytest.mark.django_db
class TestScheduledPayoutRun:
    def _released_order(self):
        order = _paid_order()
        SettlementService.release_for_order(order, confirmed=True)
        return order

    def test_the_run_pays_every_laundry_with_a_balance(self):
        order = self._released_order()

        payouts = SettlementService.run_scheduled_payouts()

        assert len(payouts) == 1
        assert payouts[0].laundry_id == order.laundry_id
        assert payouts[0].amount == order.total_amount
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_held_money_is_never_paid_out_by_the_run(self):
        # The whole point of the escrow: a scheduled job must not be the thing
        # that quietly defeats it.
        order = _paid_order()
        assert SettlementService.run_scheduled_payouts() == []

    def test_balances_under_the_minimum_are_carried_forward(self):
        # Not worth a transfer fee today; it will ride along next time.
        order = self._released_order()

        assert SettlementService.run_scheduled_payouts(minimum='1000.00') == []
        assert SettlementService.outstanding_total(order.laundry) == order.total_amount

    def test_running_twice_does_not_pay_twice(self):
        order = self._released_order()

        SettlementService.run_scheduled_payouts()
        second = SettlementService.run_scheduled_payouts()

        assert second == []
        assert Payout.objects.filter(laundry=order.laundry).count() == 1

    def test_the_run_is_quiet_when_nobody_is_owed(self):
        assert SettlementService.run_scheduled_payouts() == []

    def test_payouts_are_created_as_drafts_not_paid(self):
        # Building a payout decides who is owed what. It does not move money.
        order = self._released_order()

        payouts = SettlementService.run_scheduled_payouts()

        assert payouts[0].status == Payout.Status.DRAFT
        assert payouts[0].paid_at is None
