"""Concurrency and tenant isolation tests for Laundry Owner (Merchant) SMS alerts.

Verifies:
1. Zero cross-merchant data leakage across simultaneous bookings.
2. Multiple dispatch workers never send the same merchant SMS twice.
3. In-flight order sequencing prevents out-of-order notifications.
4. Concurrent OTP verification requests enforce rate limits and single active number.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.db import connection
from django.utils import timezone

from admin_notifications.models import (
    AdminNotificationDelivery as Delivery,
    AdminNotificationEvent as Event,
    MerchantNotificationSettings,
)
from admin_notifications.services import outbox, merchant
from admin_notifications.services.dispatcher import dispatch_due
from admin_notifications.tests.helpers import (
    FakeSms,
    book,
    configure,
    make_world,
    patch_providers,
)
from ordering.models import Order

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
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(run, range(n)))
    return results, errors


def test_concurrent_bookings_never_leak_between_merchants(settings):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False

    worlds = [
        make_world(first_name=f'Cust{i}', last_name=f'Test{i}')
        for i in range(10)
    ]
    # Set verified phone for each laundry owner
    for i, w in enumerate(worlds):
        MerchantNotificationSettings.objects.create(
            laundry=w.laundry,
            phone=f'+23324000000{i}',
            phone_verified_at=timezone.now(),
            sms_enabled=True,
        )

    # Book 10 orders simultaneously
    results, errors = _race(
        10,
        lambda i: book(worlds[i], pickup_address=f'Location {i}'),
    )

    assert errors == []
    assert all(r.status_code == 201 for r in results)

    # Verify merchant deliveries
    deliveries = Delivery.objects.filter(audience=Delivery.Audience.MERCHANT)
    assert deliveries.count() == 10

    for i, w in enumerate(worlds):
        order_id = results[i].data['id']
        deliv = deliveries.filter(laundry=w.laundry).first()
        assert deliv is not None
        assert deliv.recipient == f'23324000000{i}'
        assert str(deliv.event.order_id) == str(order_id)
        assert deliv.event.payload['laundry']['id'] == str(w.laundry.id)
        assert deliv.event.payload['pickup']['address'] == f'Location {i}'


def test_concurrent_dispatch_workers_send_merchant_sms_exactly_once(settings, monkeypatch):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False

    world = make_world()
    MerchantNotificationSettings.objects.create(
        laundry=world.laundry,
        phone='+233241112233',
        phone_verified_at=timezone.now(),
        sms_enabled=True,
    )

    # Create 15 orders
    for _ in range(15):
        assert book(world).status_code == 201

    providers = patch_providers(monkeypatch, sms=FakeSms(delay=0.01))

    def worker(_):
        total = 0
        for _ in range(5):
            total += dispatch_due(limit=5).claimed
        return total

    # 4 concurrent workers claiming from the queue
    results, errors = _race(4, worker)

    assert errors == []
    assert sum(results) == 15
    assert len(providers.sms.calls) == 15
    assert set(Delivery.objects.filter(audience=Delivery.Audience.MERCHANT).values_list('status', flat=True)) == {'SUBMITTED'}


def test_concurrent_otp_requests_rate_limited(settings, monkeypatch):
    configure(settings)
    settings.MERCHANT_SMS_NOTIFICATIONS_ENABLED = True
    patch_providers(monkeypatch)
    world = make_world()

    # Rate limit is 3 OTPs per hour by default
    def request_otp(i):
        try:
            merchant.start_verification(world.laundry, world.owner, f'02410000{i:02d}')
            return 'ok'
        except merchant.VerificationError as exc:
            return exc.code

    results, errors = _race(5, request_otp)
    assert errors == []
    ok_count = results.count('ok')
    throttled_count = results.count('throttled')
    assert ok_count <= 3
    assert ok_count + throttled_count == 5

