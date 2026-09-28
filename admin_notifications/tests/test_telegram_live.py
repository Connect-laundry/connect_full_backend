"""LIVE Telegram series: real Bot API, real messages to the ops group.

Skipped unless TELEGRAM_LIVE_TEST=1. Reads TELEGRAM_BOT_TOKEN and
ADMIN_TELEGRAM_CHAT_IDS from the environment (the git-ignored .env). Uses the
throwaway test database, never a real order. Every message is marked [TEST].

    set TELEGRAM_LIVE_TEST=1
    venv/Scripts/python.exe -m pytest admin_notifications/tests/test_telegram_live.py -v -s
"""
import os
import time
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.urls import reverse

from admin_notifications.models import AdminNotificationDelivery as Delivery
from admin_notifications.models import AdminNotificationEvent as Event
from admin_notifications.services import outbox
from admin_notifications.services.dispatcher import dispatch_due
from admin_notifications.tests.helpers import book, client_for, configure, make_world
from ordering.models import Order

LIVE = os.environ.get('TELEGRAM_LIVE_TEST') == '1'
pytestmark = [
    pytest.mark.skipif(not LIVE, reason='live Telegram test: set TELEGRAM_LIVE_TEST=1'),
    pytest.mark.django_db,
]


@pytest.fixture(autouse=True)
def live_settings(settings):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chats = os.environ.get('ADMIN_TELEGRAM_CHAT_IDS', '')
    if not token or not chats:
        pytest.fail('TELEGRAM_BOT_TOKEN and ADMIN_TELEGRAM_CHAT_IDS must be set in .env for the live test.')
    configure(settings, sms=False, whatsapp=False, environment='test')
    settings.ADMIN_TELEGRAM_NOTIFICATIONS_ENABLED = True
    settings.TELEGRAM_BOT_TOKEN = token
    settings.ADMIN_TELEGRAM_CHAT_IDS = chats
    settings.PAYSTACK_SECRET_KEY = 'test-paystack-secret'
    settings.PAYMENT_CURRENCY = 'GHS'
    yield
    time.sleep(1.5)  # stay far below Telegram's 20 messages/minute per group


def _send_all(expected_events):
    result = dispatch_due()
    rows = Delivery.objects.filter(event__in=expected_events)
    statuses = {(r.event.event_type, r.status, r.last_error_class) for r in rows.select_related('event')}
    assert result.failed == 0 and result.unknown == 0 and result.retry == 0, statuses
    for row in rows:
        assert row.status == Delivery.Status.SENT, (row.event.event_type, row.status, row.last_error_safe_message)
        assert row.provider_message_id and row.parts_sent == row.parts_total >= 1
    return rows


def _events(order, event_type):
    return Event.objects.filter(order=order, event_type=event_type)


def test_01_preflight_probe():
    out = StringIO()
    call_command('check_admin_notifications', '--probe', stdout=out)
    text = out.getvalue()
    print(text)
    assert 'token accepted' in text and 'reachable by the bot' in text
    assert os.environ['TELEGRAM_BOT_TOKEN'] not in text


def test_02_cod_booking():
    order = Order.objects.get(pk=book(make_world(first_name='Ama', last_name='Mensah'),
                                      notes='[TEST] COD itemised order').data['id'])
    _send_all(_events(order, 'NEW_ORDER'))


def test_03_paystack_pending_then_confirmed():
    from admin_notifications.tests.test_triggers import _signed_webhook
    from payments.models import Payment
    world = make_world(first_name='Kofi', last_name='Boateng')
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        order = Order.objects.get(pk=book(world, method='CARD', notes='[TEST] Paystack order').data['id'])
    _send_all(_events(order, 'NEW_ORDER'))
    time.sleep(1.5)
    payment = Payment.objects.get(order=order)
    assert _signed_webhook(payment, 'evt-live-1').status_code == 200
    assert _signed_webhook(payment, 'evt-live-1').status_code == 200  # duplicate webhook
    _send_all(_events(order, 'PAYMENT_CONFIRMED'))
    assert _events(order, 'PAYMENT_CONFIRMED').count() == 1 and _events(order, 'NEW_ORDER').count() == 1


def test_04_per_kg_estimate():
    order = Order.objects.get(pk=book(make_world(first_name='Efua', last_name='Asante'), mode='BY_WEIGHT',
                                      weight='6.50', notes='[TEST] per-kg order').data['id'])
    _send_all(_events(order, 'NEW_ORDER'))


def test_05_pay_after_quote_then_price_finalized():
    world = make_world(first_name='Yaw', last_name='Darko')
    order = Order.objects.get(pk=book(world, mode='CUSTOM_QUOTE', notes='[TEST] quote order').data['id'])
    _send_all(_events(order, 'NEW_ORDER'))
    time.sleep(1.5)
    response = client_for(world.owner).post(
        reverse('order-lifecycle-quote', kwargs={'pk': order.pk}),
        {'items': [{'name': 'Bulk wash 3kg', 'quantity': 1, 'price': '60.00'},
                   {'name': 'Duvet king size', 'quantity': 1, 'price': '45.00'}]}, format='json')
    assert response.status_code == 200
    _send_all(_events(order, 'ORDER_PRICE_FINALIZED'))


def test_06_long_order_splits_cleanly():
    world = make_world(first_name='Abena', last_name='Owusu')
    order = Order.objects.get(pk=book(world, notes='[TEST] long order').data['id'])
    event = _events(order, 'NEW_ORDER').get()
    payload = event.payload
    payload['items'] = [{'name': f'Kente wrapper {i} (dry clean only)', 'quantity': 2, 'unit_price': '17.50',
                         'line_total': '35.00'} for i in range(110)]
    Event.objects.filter(pk=event.pk).update(payload=payload)
    rows = _send_all([event])
    assert rows.get().parts_total >= 2


def test_07_customer_cancellation():
    world = make_world(first_name='Kwesi', last_name='Appiah')
    order = Order.objects.get(pk=book(world, notes='[TEST] to be cancelled').data['id'])
    _send_all(_events(order, 'NEW_ORDER'))
    time.sleep(1.5)
    url = reverse('order-lifecycle-cancel', kwargs={'pk': order.pk})
    client_for(world.customer).patch(url, {'reason': 'Travelling tomorrow'}, format='json')
    client_for(world.customer).patch(url, {'reason': 'Travelling tomorrow'}, format='json')
    _send_all(_events(order, 'ORDER_CANCELLED'))
    assert _events(order, 'ORDER_CANCELLED').count() == 1


def test_08_laundry_rejection():
    world = make_world(first_name='Adwoa', last_name='Frimpong')
    order = Order.objects.get(pk=book(world, notes='[TEST] to be rejected').data['id'])
    _send_all(_events(order, 'NEW_ORDER'))
    time.sleep(1.5)
    client_for(world.owner).patch(reverse('order-lifecycle-reject', kwargs={'pk': order.pk}),
                                  {'reason': 'Machine under repair'}, format='json')
    _send_all(_events(order, 'ORDER_REJECTED'))


def test_09_reschedule():
    order = Order.objects.get(pk=book(make_world(first_name='Nana', last_name='Agyei'),
                                      notes='[TEST] to be rescheduled').data['id'])
    _send_all(_events(order, 'NEW_ORDER'))
    time.sleep(1.5)
    order = Order.objects.get(pk=order.pk)
    order.pickup_date = order.pickup_date + timedelta(days=1)
    order.save()
    _send_all(_events(order, 'ORDER_RESCHEDULED'))


def test_10_duplicates_send_one_message():
    order = Order.objects.get(pk=book(make_world(first_name='Esi', last_name='Quaye'),
                                      notes='[TEST] duplicate protection').data['id'])
    for _ in range(10):
        outbox.emit_new_order(order)
    _send_all(_events(order, 'NEW_ORDER'))
    for _ in range(3):
        dispatch_due()  # restarts/overlapping dispatchers
    delivery = Delivery.objects.get(event__order=order)
    assert delivery.provider_messages.count() == delivery.parts_total


def test_11_wrong_chat_fails_safely_without_touching_the_group(settings):
    settings.ADMIN_TELEGRAM_CHAT_IDS = '-1000000000001'  # a chat the bot is not in
    order = Order.objects.get(pk=book(make_world()).data['id'])
    with patch('admin_notifications.services.metrics.alert'):
        dispatch_due()
    delivery = Delivery.objects.get(event__order=order)
    assert delivery.status == Delivery.Status.FAILED
    assert delivery.last_error_class == 'INVALID_RECIPIENT', delivery.last_error_safe_message
    assert order.pk and Order.objects.filter(pk=order.pk).exists()  # booking unaffected


def test_12_bad_token_fails_safely(settings):
    settings.TELEGRAM_BOT_TOKEN = '123456:definitely-not-a-real-token'
    order = Order.objects.get(pk=book(make_world()).data['id'])
    with patch('admin_notifications.services.metrics.alert'):
        dispatch_due()
    delivery = Delivery.objects.get(event__order=order)
    assert delivery.status == Delivery.Status.FAILED and delivery.last_error_class == 'AUTH_FAILED'
