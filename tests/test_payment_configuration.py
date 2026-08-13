from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import override_settings

from ordering.services.payment_service import PaymentService
from payments.checks import check_paystack_production_configuration
from payments.models import Payment
from payments.services.paystack import to_minor_units
from test_payments import _build_order


@pytest.mark.parametrize(
    ('major', 'minor'),
    [
        (Decimal('0.01'), 1),
        (Decimal('10.10'), 1010),
        ('999999.99', 99999999),
    ],
)
def test_money_conversion_never_uses_binary_float(major, minor):
    assert to_minor_units(major) == minor


@pytest.mark.django_db
@patch('payments.services.paystack.PaystackService.initialize_transaction')
def test_order_payment_intent_persists_paystack_access_code(mock_initialize):
    _, order = _build_order()
    mock_initialize.return_value = {
        'status': True,
        'data': {
            'authorization_url': 'https://checkout.paystack.com/access-code',
            'access_code': 'access-code',
        },
    }

    intent = PaymentService.create_payment_intent(order, payment_method='CARD')

    payment = Payment.objects.get(order=order)
    assert intent['transaction_id'] == payment.transaction_reference
    assert payment.paystack_reference == 'access-code'


@override_settings(
    DEBUG=False,
    PAYSTACK_SECRET_KEY='sk_test_wrong',
    PAYSTACK_PUBLIC_KEY='pk_test_wrong',
    PAYSTACK_CALLBACK_URL='connect-laundry://orders/payment-callback',
    PAYSTACK_APP_CALLBACK_URL='connect-laundry://orders/payment-callback',
    PAYMENT_CURRENCY='GHS',
)
def test_deploy_check_rejects_test_keys_and_non_https_callback():
    ids = {message.id for message in check_paystack_production_configuration(None)}

    assert {'payments.E002', 'payments.E003', 'payments.E005'} <= ids


@override_settings(
    DEBUG=False,
    PAYSTACK_SECRET_KEY='sk_live_example',
    PAYSTACK_PUBLIC_KEY='pk_live_example',
    PAYSTACK_CALLBACK_URL='https://api.simame.example/payments/callback/',
    PAYSTACK_APP_CALLBACK_URL='connect-laundry://orders/payment-callback',
    PAYMENT_CURRENCY='GHS',
)
def test_deploy_check_accepts_live_key_pair_and_https_callback():
    assert check_paystack_production_configuration(None) == []