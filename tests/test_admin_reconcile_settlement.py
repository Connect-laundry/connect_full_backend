"""Admin "Force Reconcile via Paystack" must have the same financial consequences
as the customer verify endpoint: settlement (laundry owed), platform commission,
transport and promo deductions, payout eligibility, audit and ops alert.

Regression for a P1: the admin action marked orders PAID and CONFIRMED but never
recorded the settlement, so the laundry was owed nothing.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from admin_notifications.models import AdminNotificationEvent
from admin_notifications.tests.helpers import book, client_for, configure, make_world
from marketplace.models import AuditLog
from ordering.models import Order
from payments.models import OrderSettlement, Payment
from users.models import User

pytestmark = pytest.mark.django_db

SETTLEMENT_FIELDS = ('gross_amount', 'platform_commission', 'processor_fee', 'logistics_subsidy_deducted',
                     'net_payable', 'currency', 'route', 'status')


@pytest.fixture
def world(settings):
    configure(settings)
    return make_world()


def _pending_card_order(world):
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        order = Order.objects.get(pk=book(world, method='CARD').data['id'])
    # Transport billed in-app and a laundry-funded free-transport promo, so
    # every settlement deduction is exercised.
    Order.objects.filter(pk=order.pk).update(
        delivery_fees_in_app=True, pickup_fee=Decimal('6.00'), delivery_fee=Decimal('4.00'),
        is_free_delivery_promo=True, promo_funding_source='LAUNDRY', logistics_discount=Decimal('3.00'),
        total_amount=order.total_amount + Decimal('10.00'),
    )
    order.refresh_from_db()
    payment = Payment.objects.get(order=order)
    Payment.objects.filter(pk=payment.pk).update(amount=order.total_amount)
    payment.refresh_from_db()
    return order, payment


def _gateway(order, payment):
    return {'status': True, 'data': {
        'status': 'success', 'reference': payment.transaction_reference, 'amount': int(payment.amount * 100),
        'currency': 'GHS', 'channel': 'mobile_money', 'fees': 150,
        'metadata': {'order_id': str(order.pk), 'user_id': str(order.user_id)},
    }}


def _staff_client():
    user = User.objects.create_superuser(email='finance-ops@example.com', phone='0209998811', password='Strong-Pass-123')
    client = Client()
    client.force_login(user)
    return client, user


def _admin_reconcile(client, payment):
    return client.post(reverse('admin:payments_payment_changelist'),
                       {'action': 'force_reconcile', '_selected_action': [str(payment.pk)]})


def _snapshot(order):
    settlement = OrderSettlement.objects.get(order=order)
    payment = Payment.objects.get(order=order)
    order.refresh_from_db()
    return {
        'settlement': {f: getattr(settlement, f) for f in SETTLEMENT_FIELDS},
        'payment': (payment.status, payment.payment_method, payment.paid_at is not None),
        'order': (order.status, order.payment_status),
    }


def test_admin_reconcile_matches_customer_verify_financially(world):
    verified_order, verified_payment = _pending_card_order(world)
    admin_order, admin_payment = _pending_card_order(world)
    assert verified_order.total_amount == admin_order.total_amount

    with patch('payments.services.paystack.PaystackService.verify_transaction',
               return_value=_gateway(verified_order, verified_payment)):
        response = client_for(world.customer).get(
            reverse('payment_verify', kwargs={'reference': verified_payment.transaction_reference}))
        assert response.status_code == 200, response.data

    client, staff = _staff_client()
    with patch('payments.services.paystack.PaystackService.verify_transaction',
               return_value=_gateway(admin_order, admin_payment)):
        assert _admin_reconcile(client, admin_payment).status_code == 302

    normal, manual = _snapshot(verified_order), _snapshot(admin_order)
    assert manual == normal
    s = manual['settlement']
    # Laundry owed = total - in-app transport (rider's) - commission - laundry-funded promo.
    expected_gross = admin_order.total_amount - Decimal('10.00')
    assert s['gross_amount'] == expected_gross
    assert s['logistics_subsidy_deducted'] == Decimal('3.00')
    assert s['processor_fee'] == Decimal('1.50')
    assert s['net_payable'] == expected_gross - s['platform_commission'] - Decimal('3.00')
    assert s['status'] == OrderSettlement.Status.HELD  # payout only after delivery, same as normal path
    assert manual['payment'] == (Payment.Status.SUCCESS, Payment.Method.MOMO, True)
    assert manual['order'] == (Order.Status.CONFIRMED, Order.PaymentStatus.PAID)

    assert AuditLog.objects.filter(action='PAYMENT_ADMIN_RECONCILED', target_id=str(admin_payment.pk)).exists()
    assert AdminNotificationEvent.objects.filter(order=admin_order, event_type='PAYMENT_CONFIRMED').count() == 1


def test_admin_reconcile_is_idempotent(world):
    order, payment = _pending_card_order(world)
    client, _ = _staff_client()
    with patch('payments.services.paystack.PaystackService.verify_transaction',
               return_value=_gateway(order, payment)) as verify:
        _admin_reconcile(client, payment)
        _admin_reconcile(client, payment)  # payment no longer PENDING: skipped
    assert verify.call_count == 1
    assert OrderSettlement.objects.filter(order=order).count() == 1
    assert AdminNotificationEvent.objects.filter(order=order, event_type='PAYMENT_CONFIRMED').count() == 1


def test_admin_reconcile_after_webhook_does_not_double_credit(world):
    from payments.services.verified_payment import ALREADY_DONE, apply_verified_paystack_success
    from django.db import transaction
    order, payment = _pending_card_order(world)
    with transaction.atomic():
        assert apply_verified_paystack_success(payment.pk, _gateway(order, payment)['data'])[0] == 'applied'
    with transaction.atomic():
        assert apply_verified_paystack_success(payment.pk, _gateway(order, payment)['data'])[0] == ALREADY_DONE
    assert OrderSettlement.objects.filter(order=order).count() == 1


def test_admin_reconcile_rejects_amount_mismatch_without_money_records(world):
    order, payment = _pending_card_order(world)
    gateway = _gateway(order, payment)
    gateway['data']['amount'] = 100  # GHS 1.00 paid for a larger order
    client, _ = _staff_client()
    with patch('payments.services.paystack.PaystackService.verify_transaction', return_value=gateway):
        _admin_reconcile(client, payment)
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.payment_status == Order.PaymentStatus.UNPAID and order.status == Order.Status.PENDING
    assert not OrderSettlement.objects.filter(order=order).exists()


def test_admin_reconcile_abandoned_marks_failed_only(world):
    order, payment = _pending_card_order(world)
    client, _ = _staff_client()
    with patch('payments.services.paystack.PaystackService.verify_transaction',
               return_value={'status': True, 'data': {'status': 'abandoned'}}):
        _admin_reconcile(client, payment)
    payment.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert not OrderSettlement.objects.filter(order=order).exists()
