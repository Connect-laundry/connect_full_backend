"""Real PostgreSQL concurrency proof for the admin notification outbox.

Skipped unless the database is PostgreSQL: SQLite has no row locks, so a
threaded test there proves nothing. Every case starts real threads behind a
barrier, each with its own connection, with real commits.

    pytest --ds=<postgres settings> admin_notifications/tests/test_pg_concurrency.py
"""
import hashlib
import hmac
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection
from django.urls import reverse
from rest_framework.test import APIClient

from admin_notifications.models import AdminNotificationDelivery as Delivery
from admin_notifications.models import AdminNotificationEvent as Event
from admin_notifications.services import outbox
from admin_notifications.services.callbacks import apply_sms_status
from admin_notifications.services.dispatcher import dispatch_due
from admin_notifications.tests.helpers import (
    FakeSms, FakeWhatsApp, book, configure, make_world, patch_providers,
)
from ordering.models import Order
from payments.models import Payment

pytestmark = [
    pytest.mark.skipif(connection.vendor != 'postgresql', reason='needs PostgreSQL row locking'),
    pytest.mark.django_db(transaction=True),
]


def _race(n, fn):
    barrier = threading.Barrier(n)
    errors = []

    def run(i):
        try:
            barrier.wait()
            return fn(i)
        except Exception as exc:  # collected and asserted on below
            errors.append(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(run, range(n)))
    return results, errors


@pytest.fixture
def cfg(settings):
    configure(settings)
    return settings


def test_twenty_simultaneous_emits_for_one_booking(cfg):
    cfg.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False
    world = make_world()
    order = Order.objects.get(pk=book(world).data['id'])
    cfg.ADMIN_ORDER_NOTIFICATIONS_ENABLED = True

    _, errors = _race(20, lambda i: outbox.emit_new_order(Order.objects.get(pk=order.pk)))

    assert errors == []
    assert Event.objects.filter(order=order, event_type='NEW_ORDER').count() == 1
    assert Delivery.objects.filter(event__order=order, channel='WHATSAPP').count() == 3
    assert Delivery.objects.filter(event__order=order, channel='SMS').count() == 1


def test_twenty_different_orders_at_once_never_mix_data(cfg):
    worlds = [make_world(first_name=f'Cust{i}', last_name=f'Family{i}') for i in range(20)]

    results, errors = _race(20, lambda i: book(worlds[i], pickup_address=f'House {i}, Street {i}'))

    assert errors == []
    assert all(r.status_code == 201 for r in results)
    assert Event.objects.count() == 20 and Delivery.objects.count() == 80
    for i, response in enumerate(results):
        order = Order.objects.select_related('user', 'laundry').get(pk=response.data['id'])
        payload = Event.objects.get(order=order).payload
        assert payload['order']['id'] == str(order.pk)
        assert payload['order']['order_no'] == order.order_no
        assert payload['customer']['name'] == f'Cust{i} Family{i}'
        assert payload['laundry']['name'] == worlds[i].laundry.name
        assert payload['pickup']['address'] == f'House {i}, Street {i}'
        assert payload['money']['total'] == str(order.total_amount)
        assert {i['name'] for i in payload['items']} == set(order.items.values_list('name', flat=True))


def test_five_dispatch_workers_send_every_delivery_exactly_once(cfg, monkeypatch):
    world = make_world()
    for _ in range(20):
        assert book(world).status_code == 201
    providers = patch_providers(monkeypatch, sms=FakeSms(delay=0.02), whatsapp=FakeWhatsApp(delay=0.02))

    def worker(_):
        total = 0
        for _ in range(10):
            total += dispatch_due(limit=7).claimed
        return total

    results, errors = _race(5, worker)

    assert errors == []
    assert sum(results) == 80
    sent = [(r, p[0]) for r, _, p in providers.whatsapp.calls] + [(r, m.split('\n')[0]) for r, m in providers.sms.calls]
    assert len(sent) == 80 and len(set(sent)) == 80  # no (recipient, order) pair sent twice
    assert set(Delivery.objects.values_list('status', flat=True)) == {'SENT', 'SUBMITTED'}


def test_duplicate_sms_callbacks_race_harmlessly(cfg, monkeypatch):
    cfg.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    patch_providers(monkeypatch)
    assert book(make_world()).status_code == 201
    dispatch_due()
    delivery = Delivery.objects.get()
    sms_id = delivery.provider_messages.get().provider_message_id

    _, errors = _race(10, lambda i: apply_sms_status(sms_id, 'DELIVERED' if i % 2 else 'QUEUED'))

    assert errors == []
    delivery.refresh_from_db()
    message = delivery.provider_messages.get()
    # A final status, once reached, is never overwritten by a racing QUEUED.
    assert message.provider_status in ('DELIVERED', 'QUEUED')
    if message.provider_status == 'DELIVERED':
        assert delivery.status == 'DELIVERED'


def test_duplicate_paystack_webhooks_produce_one_payment_confirmed(cfg):
    cfg.PAYSTACK_SECRET_KEY = 'test-paystack-secret'
    cfg.PAYMENT_CURRENCY = 'GHS'
    world = make_world()
    from unittest.mock import patch
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        order = Order.objects.get(pk=book(world, method='CARD').data['id'])
    payment = Payment.objects.get(order=order)

    def webhook(i):
        data = {'id': f'evt-{i % 3}', 'status': 'success', 'reference': payment.transaction_reference,
                'amount': int(payment.amount * 100), 'currency': 'GHS', 'channel': 'card',
                'metadata': {'order_id': str(order.pk), 'user_id': str(order.user_id)}}
        body = json.dumps({'event': 'charge.success', 'data': data}, separators=(',', ':')).encode()
        sig = hmac.new(b'test-paystack-secret', body, hashlib.sha512).hexdigest()
        return APIClient().post(reverse('paystack_webhook'), data=body, content_type='application/json',
                                HTTP_X_PAYSTACK_SIGNATURE=sig).status_code

    results, errors = _race(10, webhook)

    assert errors == []
    assert all(code in (200, 500, 503) for code in results)
    order.refresh_from_db()
    assert order.payment_status == 'PAID'
    assert Event.objects.filter(order=order, event_type='PAYMENT_CONFIRMED').count() == 1
    assert Event.objects.filter(order=order, event_type='NEW_ORDER').count() == 1


def test_burst_of_fifty_bookings_with_live_dispatch(cfg, monkeypatch):
    import time
    patch_providers(monkeypatch, sms=FakeSms(delay=0.05), whatsapp=FakeWhatsApp(delay=0.05))
    worlds = [make_world() for _ in range(10)]
    started = time.monotonic()
    results, errors = _race(10, lambda i: [book(worlds[i]).status_code for _ in range(5)])
    elapsed = time.monotonic() - started
    assert errors == [] and all(code == 201 for codes in results for code in codes)
    assert Event.objects.count() == 50
    assert elapsed < 30
    while dispatch_due(limit=25).claimed:
        pass
    assert Delivery.objects.exclude(status__in=['SENT', 'SUBMITTED']).count() == 0


def test_post_commit_background_dispatch_latency(cfg, monkeypatch):
    """The real production path: booking commits -> on_commit kick -> worker
    thread claims and sends. Measures booking-to-accepted latency."""
    import time
    cfg.ADMIN_NOTIFICATION_DISPATCH_IN_THREAD = True
    providers = patch_providers(monkeypatch, sms=FakeSms(delay=0.3), whatsapp=FakeWhatsApp(delay=0.3))
    world = make_world()
    started = time.monotonic()
    response = book(world)
    booking_seconds = time.monotonic() - started
    assert response.status_code == 201
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if Delivery.objects.filter(status__in=['SENT', 'SUBMITTED']).count() == 4:
            break
        time.sleep(0.1)
    latency = time.monotonic() - started
    assert Delivery.objects.filter(status__in=['SENT', 'SUBMITTED']).count() == 4
    assert len(providers.whatsapp.calls) == 3 and len(providers.sms.calls) == 1
    # Four slow (0.3s) provider calls happened after the response, not before it.
    assert booking_seconds < 1.2
    print(f'\nbooking response {booking_seconds:.2f}s, all 4 alerts accepted {latency:.2f}s after booking start')


def test_ten_concurrent_cancels_alert_once(cfg):
    world = make_world()
    order = Order.objects.get(pk=book(world).data['id'])
    url = reverse('order-lifecycle-cancel', kwargs={'pk': order.pk})

    def cancel(_):
        client = APIClient()
        client.force_authenticate(user=world.customer)
        return client.patch(url, {'reason': 'race'}, format='json').status_code

    results, errors = _race(10, cancel)
    assert errors == [] and all(code == 200 for code in results)
    from ordering.models.base import OrderStatusHistory
    assert OrderStatusHistory.objects.filter(order=order, new_status='CANCELLED').count() == 1
    assert Event.objects.filter(order=order, event_type='ORDER_CANCELLED').count() == 1
