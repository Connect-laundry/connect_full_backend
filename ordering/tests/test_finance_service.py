import pytest
from decimal import Decimal
from unittest.mock import MagicMock
from ordering.services.finance_service import FinanceService


@pytest.fixture
def mock_order():
    order = MagicMock()
    order.items.aggregate.return_value = {'total': Decimal('100.00')}
    order.laundry.delivery_fee = Decimal('10.00')
    order.laundry.pickup_fee = Decimal('0.00')
    order.coupon = None
    return order


@pytest.fixture
def logistics_billed_in_app(settings):
    """Option B: the app quotes and collects the laundry's logistics fee."""
    settings.DELIVERY_FEES_IN_APP = True
    return settings


@pytest.fixture
def logistics_settled_directly(settings):
    """Option A (current): the customer pays the laundry for pickup/delivery."""
    settings.DELIVERY_FEES_IN_APP = False
    return settings


def test_calculate_tax_amount():
    # Test with default tax rate (0.07)
    amount = Decimal('100.00')
    tax = FinanceService.calculate_tax_amount(amount)
    assert tax == Decimal('7.00')

    # Test with explicit tax rate
    tax = FinanceService.calculate_tax_amount(amount, tax_rate='0.05')
    assert tax == Decimal('5.00')


class TestLogisticsSettledDirectly:
    """
    While the platform runs no couriers, it must not charge for delivery.

    The laundry may still have a fee configured on its profile; that fee is
    what it collects from the customer in person, and the app has no business
    adding it to a card payment it would have no way to forward.
    """

    def test_delivery_fee_is_not_charged(self, mock_order, logistics_settled_directly):
        assert FinanceService.calculate_delivery_fee(mock_order) == Decimal('0.00')

    def test_pickup_fee_is_not_charged(self, mock_order, logistics_settled_directly):
        mock_order.laundry.pickup_fee = Decimal('8.00')
        assert FinanceService.calculate_pickup_fee(mock_order) == Decimal('0.00')

    def test_breakdown_excludes_logistics_from_the_total(
        self, mock_order, logistics_settled_directly
    ):
        breakdown = FinanceService.calculate_price_breakdown(mock_order)

        assert breakdown['items_total'] == '100.00'
        assert breakdown['delivery_fee'] == '0.00'
        assert breakdown['pickup_fee'] == '0.00'
        # tax 7.00 + platform fee 5.00, no logistics
        assert breakdown['total'] == '112.00'

    def test_breakdown_says_logistics_are_not_billed_here(
        self, mock_order, logistics_settled_directly
    ):
        # Without this flag a client cannot tell "free delivery" apart from
        # "delivery is arranged with the laundry", and 0.00 reads as free.
        breakdown = FinanceService.calculate_price_breakdown(mock_order)
        assert breakdown['delivery_fees_in_app'] is False


class TestLogisticsBilledInApp:
    """Option B, switched on by DELIVERY_FEES_IN_APP once payouts exist."""

    def test_delivery_fee_comes_from_the_laundry(
        self, mock_order, logistics_billed_in_app
    ):
        assert FinanceService.calculate_delivery_fee(mock_order) == Decimal('10.00')

    def test_no_platform_fallback_when_the_laundry_has_no_fee(
        self, mock_order, logistics_billed_in_app
    ):
        # The platform performs no deliveries, so it can never be the origin of
        # a delivery charge. A laundry with nothing configured charges nothing.
        mock_order.laundry.delivery_fee = None
        assert FinanceService.calculate_delivery_fee(mock_order) == Decimal('0.00')

    def test_breakdown_includes_logistics(self, mock_order, logistics_billed_in_app):
        breakdown = FinanceService.calculate_price_breakdown(mock_order)

        assert breakdown['items_total'] == '100.00'
        assert breakdown['delivery_fee'] == '10.00'
        assert breakdown['discount'] == '0.00'
        # tax = 100 * 0.07 = 7.00
        assert breakdown['tax'] == '7.00'
        # platform_fee = 100 * 0.05 = 5.00
        assert breakdown['platform_fee'] == '5.00'
        # total = 100 + 10 + 7 + 5 = 122.00
        assert breakdown['total'] == '122.00'
        assert breakdown['delivery_fees_in_app'] is True

    def test_breakdown_with_coupon(self, mock_order, logistics_billed_in_app):
        coupon = MagicMock()
        coupon.discount_type = 'FIXED'
        coupon.discount_value = Decimal('20.00')
        coupon.is_valid.return_value = (True, None)

        breakdown = FinanceService.calculate_price_breakdown(mock_order, coupon=coupon)

        assert breakdown['discount'] == '20.00'
        # taxable_amount = 100 - 20 = 80.00
        # tax = 80 * 0.07 = 5.60
        assert breakdown['tax'] == '5.60'
        # total = 80 + 10 + 5.60 + (80 * 0.05=4.00) = 99.60
        assert breakdown['total'] == '99.60'


def test_coupon_discount_applies_without_logistics_fees(
    mock_order, logistics_settled_directly
):
    coupon = MagicMock()
    coupon.discount_type = 'FIXED'
    coupon.discount_value = Decimal('20.00')
    coupon.is_valid.return_value = (True, None)

    breakdown = FinanceService.calculate_price_breakdown(mock_order, coupon=coupon)

    assert breakdown['discount'] == '20.00'
    assert breakdown['tax'] == '5.60'
    # 80 + 5.60 tax + 4.00 platform fee, no delivery
    assert breakdown['total'] == '89.60'
