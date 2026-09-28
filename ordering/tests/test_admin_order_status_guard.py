"""Django admin can no longer change an order's status (or money/ownership) as a
raw field; status changes go through the lifecycle with its refunds, settlement
rules and operations alerts."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from admin_notifications.models import AdminNotificationEvent
from admin_notifications.tests.helpers import book, configure, make_world
from ordering.models import Order
from ordering.models.base import OrderStatusHistory
from payments.models import Payment
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    user = User.objects.create_superuser(email='ops-staff@example.com', phone='0209998822', password='Strong-Pass-123')
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def world(settings):
    configure(settings)
    return make_world()


def _change_form_post(client, order, **fields):
    url = reverse('admin:ordering_order_change', args=[order.pk])
    data = {
        'order_no': order.order_no, 'user': str(order.user_id), 'laundry': str(order.laundry_id),
        'status': order.status, 'total_amount': str(order.total_amount),
        'items-TOTAL_FORMS': '0', 'items-INITIAL_FORMS': str(order.items.count()),
        'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
    }
    data.update(fields)
    return client.post(url, data)


@pytest.mark.parametrize('target', ['CANCELLED', 'REJECTED', 'COMPLETED', 'DELIVERED'])
def test_raw_status_edit_is_ignored(staff, world, target):
    order = Order.objects.get(pk=book(world).data['id'])
    other = make_world()
    response = _change_form_post(staff, order, status=target, total_amount='1.00', laundry=str(other.laundry.pk))
    assert response.status_code == 302  # the save went through; the raw fields were ignored
    order.refresh_from_db()
    assert order.status == 'PENDING'
    assert order.total_amount != Decimal('1.00')
    assert order.laundry_id == world.laundry.pk
    assert not OrderStatusHistory.objects.filter(order=order).exists()
    assert list(AdminNotificationEvent.objects.filter(order=order).values_list('event_type', flat=True)) == ['NEW_ORDER']


def test_status_field_is_read_only_on_the_change_page(staff, world):
    order = Order.objects.get(pk=book(world).data['id'])
    html = staff.get(reverse('admin:ordering_order_change', args=[order.pk])).content.decode()
    assert 'name="status"' not in html and 'name="total_amount"' not in html and 'name="laundry"' not in html


def test_admin_cancel_action_uses_lifecycle_refund_and_alert(staff, world):
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        order = Order.objects.get(pk=book(world, method='CARD').data['id'])
    Payment.objects.filter(order=order).update(status=Payment.Status.SUCCESS)
    Order.objects.filter(pk=order.pk).update(payment_status='PAID', status='CONFIRMED')
    with patch('payments.services.refund.refund_payment') as refund:
        response = staff.post(reverse('admin:ordering_order_changelist'),
                              {'action': 'cancel_orders', '_selected_action': [str(order.pk)]})
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.status == 'CANCELLED' and order.cancellation_reason == 'Cancelled by Simame operations'
    assert refund.called
    assert OrderStatusHistory.objects.filter(order=order, new_status='CANCELLED').count() == 1
    assert AdminNotificationEvent.objects.filter(order=order, event_type='ORDER_CANCELLED').count() == 1


def test_admin_cancel_refuses_illegal_transition_without_refund(staff, world):
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        order = Order.objects.get(pk=book(world, method='CARD').data['id'])
    Payment.objects.filter(order=order).update(status=Payment.Status.SUCCESS)
    Order.objects.filter(pk=order.pk).update(payment_status='PAID', status='DELIVERED')
    with patch('payments.services.refund.refund_payment') as refund:
        staff.post(reverse('admin:ordering_order_changelist'),
                   {'action': 'cancel_orders', '_selected_action': [str(order.pk)]})
    order.refresh_from_db()
    assert order.status == 'DELIVERED' and not refund.called
    assert not AdminNotificationEvent.objects.filter(order=order, event_type='ORDER_CANCELLED').exists()


def test_admin_reject_action_alerts_ops(staff, world):
    order = Order.objects.get(pk=book(world).data['id'])
    staff.post(reverse('admin:ordering_order_changelist'),
               {'action': 'reject_orders', '_selected_action': [str(order.pk)]})
    order.refresh_from_db()
    assert order.status == 'REJECTED'
    assert AdminNotificationEvent.objects.filter(order=order, event_type='ORDER_REJECTED').count() == 1


def test_view_only_staff_cannot_run_transition_actions(world):
    from django.contrib.auth.models import Permission
    viewer = User.objects.create_user(email='viewer@example.com', phone='0209998833', password='Strong-Pass-123',
                                      is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename='view_order'))
    client = Client()
    client.force_login(viewer)
    order = Order.objects.get(pk=book(world).data['id'])
    client.post(reverse('admin:ordering_order_changelist'),
                {'action': 'cancel_orders', '_selected_action': [str(order.pk)]})
    order.refresh_from_db()
    assert order.status == 'PENDING'
