"""
Routing a customer's payment to the laundry's own Paystack subaccount.

The safety property under test is the fallback direction: anything missing or
misconfigured must send the money to the platform account, where it is merely
held and recorded as owed. Failing the other way would send money somewhere
unintended, which is not recoverable with an API call.
"""

from decimal import Decimal

import pytest
from django.test import override_settings

from ordering.services.finance_service import FinanceService
from payments.models import OrderSettlement
from payments.services.settlement_service import SettlementService
from payments.services.split_routing import (
    platform_charge_pesewas,
    resolve_route,
    split_payments_enabled,
)

from test_payments import _build_order


def _enable(laundry, code='ACCT_test123'):
    laundry.paystack_subaccount_code = code
    laundry.split_payments_enabled = True
    laundry.save(update_fields=['paystack_subaccount_code', 'split_payments_enabled'])
    return laundry


@pytest.mark.django_db
class TestRouteResolution:
    @override_settings(PAYSTACK_SPLIT_ENABLED=True)
    def test_a_fully_configured_laundry_settles_directly(self):
        _, order = _build_order()
        _enable(order.laundry)

        route = resolve_route(order)

        assert route.is_direct
        assert route.subaccount_code == 'ACCT_test123'

    @override_settings(PAYSTACK_SPLIT_ENABLED=False)
    def test_the_global_switch_overrides_everything(self):
        # One flag has to be able to stop all direct settlement at once.
        _, order = _build_order()
        _enable(order.laundry)

        assert resolve_route(order).is_direct is False

    @override_settings(PAYSTACK_SPLIT_ENABLED=True)
    def test_a_laundry_not_individually_enabled_is_not_split(self):
        _, order = _build_order()
        order.laundry.paystack_subaccount_code = 'ACCT_test123'
        order.laundry.save(update_fields=['paystack_subaccount_code'])

        assert resolve_route(order).is_direct is False

    @override_settings(PAYSTACK_SPLIT_ENABLED=True)
    def test_an_enabled_laundry_with_no_subaccount_falls_back(self):
        # Misconfiguration must not lose the money. Platform collection is
        # recoverable; sending to an empty subaccount code is not.
        _, order = _build_order()
        order.laundry.split_payments_enabled = True
        order.laundry.save(update_fields=['split_payments_enabled'])

        assert resolve_route(order).is_direct is False

    @override_settings(PAYSTACK_SPLIT_ENABLED=True)
    def test_a_blank_subaccount_code_is_treated_as_missing(self):
        _, order = _build_order()
        _enable(order.laundry, code='   ')

        assert resolve_route(order).is_direct is False

    def test_split_is_disabled_by_default(self):
        # Nobody should have to remember to turn this off.
        assert split_payments_enabled() is False


@pytest.mark.django_db
class TestPlatformCharge:
    def test_the_platform_takes_nothing_while_the_app_is_free(self):
        _, order = _build_order()
        with override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00):
            FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        assert platform_charge_pesewas(order) == 0

    def test_the_charge_comes_from_the_frozen_snapshot(self):
        # Not recomputed: the amount split out must match what the customer
        # was actually shown and charged.
        _, order = _build_order()
        with override_settings(PLATFORM_FEE_RATE=0.10, TAX_RATE=0.00):
            FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        # 10% of GHS 25.00 is GHS 2.50, which Paystack wants as 250 pesewas.
        assert platform_charge_pesewas(order) == 250

        # Raising the rate later must not change this order's split.
        with override_settings(PLATFORM_FEE_RATE=0.50):
            assert platform_charge_pesewas(order) == 250

    def test_an_unpriced_order_charges_nothing(self):
        _, order = _build_order()
        order.platform_fee = None
        assert platform_charge_pesewas(order) == 0


@pytest.mark.django_db
class TestDirectSettlementLedger:
    def test_a_direct_payment_owes_the_laundry_nothing_afterwards(self):
        # The money never reached the platform, so there is no debt to pay out.
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order, settled_directly=True)

        assert settlement.route == OrderSettlement.Route.DIRECT
        assert settlement.status == OrderSettlement.Status.PAID
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

    def test_a_direct_payment_is_still_recorded_for_reporting(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order, settled_directly=True)

        assert settlement.gross_amount == order.total_amount
        assert OrderSettlement.objects.filter(order=order).count() == 1

    def test_a_platform_collected_payment_is_held_until_delivery(self):
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order, settled_directly=False)

        assert settlement.route == OrderSettlement.Route.PLATFORM
        assert settlement.status == OrderSettlement.Status.HELD
        assert SettlementService.outstanding_total(order.laundry) == Decimal('0.00')

        SettlementService.release_for_order(order)
        assert SettlementService.outstanding_total(order.laundry) == settlement.net_payable

    def test_direct_settlement_is_not_held(self):
        # Paystack already sent the money, so there is nothing left to hold.
        # The escrow protection does not apply on this route, which is the
        # trade-off for instant settlement.
        _, order = _build_order()
        FinanceService.freeze_price_breakdown(order)
        order.refresh_from_db()

        settlement = SettlementService.record_for_order(order, settled_directly=True)

        assert settlement.status == OrderSettlement.Status.PAID
        assert SettlementService.held_total(order.laundry) == Decimal('0.00')
