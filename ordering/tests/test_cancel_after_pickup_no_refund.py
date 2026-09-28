"""A cancel that the state machine will refuse must not move money first.

Regression: the lifecycle cancel/reject path issued the Paystack refund before
checking the transition was legal, so a customer could "cancel" a paid order
already PICKED_UP (or later): the request returned 400 "Invalid state
transition", the order carried on, and the refund had already been started.
"""
from unittest.mock import patch

import pytest
from django.urls import reverse

from admin_notifications.tests.helpers import book, client_for, make_world
from ordering.models import Order
from payments.models import OrderSettlement, Payment

pytestmark = pytest.mark.django_db


def _paid_order(world, status):
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        order = Order.objects.get(pk=book(world, method='CARD').data['id'])
    Payment.objects.filter(order=order).update(status=Payment.Status.SUCCESS)
    Order.objects.filter(pk=order.pk).update(payment_status='PAID', status=status)
    return order


@pytest.mark.parametrize('status', ['PICKED_UP', 'IN_PROCESS', 'OUT_FOR_DELIVERY', 'DELIVERED', 'COMPLETED'])
@pytest.mark.parametrize('action,actor', [('cancel', 'customer'), ('reject', 'owner')])
def test_illegal_cancel_or_reject_never_refunds(status, action, actor):
    world = make_world()
    order = _paid_order(world, status)
    user = world.customer if actor == 'customer' else world.owner
    with patch('payments.services.refund.refund_payment') as refund, \
            patch('payments.services.paystack.PaystackService.refund_transaction') as provider_refund:
        response = client_for(user).patch(
            reverse(f'order-lifecycle-{action}', kwargs={'pk': order.pk}), {'reason': 'x'}, format='json')
    assert response.status_code == 400
    assert not refund.called and not provider_refund.called
    order.refresh_from_db()
    assert order.status == status and order.payment_status == 'PAID'
    assert Payment.objects.get(order=order).status == Payment.Status.SUCCESS


def test_legal_cancel_of_paid_order_still_refunds():
    world = make_world()
    order = _paid_order(world, 'CONFIRMED')
    with patch('payments.services.refund.refund_payment') as refund:
        response = client_for(world.customer).patch(
            reverse('order-lifecycle-cancel', kwargs={'pk': order.pk}), {'reason': 'x'}, format='json')
    assert response.status_code == 200
    assert refund.called
    order.refresh_from_db()
    assert order.status == 'CANCELLED'
