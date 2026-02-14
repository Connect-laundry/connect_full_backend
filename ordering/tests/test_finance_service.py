import pytest
from decimal import Decimal
from django.conf import settings
from unittest.mock import MagicMock
from ordering.services.finance_service import FinanceService

@pytest.fixture
def mock_order():
    order = MagicMock()
    order.items.aggregate.return_value = {'total': Decimal('100.00')}
    order.laundry.delivery_fee = Decimal('10.00')
    order.coupon = None
    return order

def test_calculate_tax_amount():
    # Test with default tax rate (0.07)
    amount = Decimal('100.00')
    tax = FinanceService.calculate_tax_amount(amount)
    assert tax == Decimal('7.00')

    # Test with explicit tax rate
    tax = FinanceService.calculate_tax_amount(amount, tax_rate='0.05')
    assert tax == Decimal('5.00')

def test_calculate_delivery_fee(mock_order):
    fee = FinanceService.calculate_delivery_fee(mock_order)
    assert fee == Decimal('10.00')

    # Test fallback
    mock_order.laundry.delivery_fee = None
    fee = FinanceService.calculate_delivery_fee(mock_order)
    assert fee == Decimal(str(settings.DELIVERY_FEE_BASE))

def test_calculate_price_breakdown(mock_order):
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

def test_calculate_price_breakdown_with_coupon(mock_order):
    coupon = MagicMock()
    coupon.discount_type = 'FIXED'
    coupon.discount_value = Decimal('20.00')
    
    breakdown = FinanceService.calculate_price_breakdown(mock_order, coupon=coupon)
    
    assert breakdown['discount'] == '20.00'
    # taxable_amount = 100 - 20 = 80.00
    # tax = 80 * 0.07 = 5.60
    assert breakdown['tax'] == '5.60'
    # total = 80 + 10 + 5.60 + (80 * 0.05=4.00) = 99.60
    assert breakdown['total'] == '99.60'
