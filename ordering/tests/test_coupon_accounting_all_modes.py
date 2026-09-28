"""Coupon usage is counted for every pricing mode, so limits cannot be bypassed.

Regression: by-weight and pay-after-quote bookings applied a coupon's discount
without recording CouponUsage or incrementing current_usage.
"""
import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from admin_notifications.tests.helpers import make_world
from ordering.models import Coupon, CouponUsage, Order
from users.models import User


def _coupon(**overrides):
    fields = dict(code=f'SAVE{timezone.now().timestamp():.0f}{User.objects.count()}', discount_type='FIXED',
                  discount_value=Decimal('5.00'), max_usage=None, user_limit=1, is_active=True)
    fields.update(overrides)
    return Coupon.objects.create(**fields)


def _book_with(world, coupon, mode='BY_ITEM', customer=None):
    if customer is not None:
        world.customer = customer
    from django.urls import reverse
    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=world.customer)
    body = {
        'laundry': str(world.laundry.id),
        'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
        'pickup_address': 'KNUST', 'delivery_address': 'KNUST', 'payment_method': 'CASH',
        'pricing_mode': mode, 'coupon_code': coupon.code,
    }
    if mode == 'BY_ITEM':
        body['items'] = [{'item': str(world.service.item_id), 'service_type': str(world.service.service_type_id),
                          'quantity': 2}]
    if mode == 'BY_WEIGHT':
        body['estimated_weight_kg'] = '4.00'
    return client.post(reverse('booking-create'), body, format='json')


def _another_customer(n):
    return User.objects.create_user(email=f'coupon-cust-{n}-{timezone.now().timestamp()}@example.com',
                                    phone=f'0209{n:06d}', password='StrongPass123!')


@pytest.mark.django_db
@pytest.mark.parametrize('mode', ['BY_ITEM', 'BY_WEIGHT', 'CUSTOM_QUOTE'])
def test_first_use_is_counted_in_every_mode(mode):
    world = make_world()
    coupon = _coupon()
    response = _book_with(world, coupon, mode=mode)
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data['id'])
    assert order.coupon_id == coupon.pk
    assert CouponUsage.objects.filter(coupon=coupon, order=order, user=world.customer).count() == 1
    coupon.refresh_from_db()
    assert coupon.current_usage == 1
    if mode != 'CUSTOM_QUOTE':
        assert order.discount_amount == Decimal('5.00')


@pytest.mark.django_db
@pytest.mark.parametrize('mode', ['BY_ITEM', 'BY_WEIGHT'])
def test_second_use_by_same_customer_is_refused(mode):
    world = make_world()
    coupon = _coupon(user_limit=1)
    assert _book_with(world, coupon, mode=mode).status_code == 201
    second = _book_with(world, coupon, mode=mode)
    assert second.status_code == 400 and 'coupon_code' in second.data
    coupon.refresh_from_db()
    assert coupon.current_usage == 1 and CouponUsage.objects.filter(coupon=coupon).count() == 1


@pytest.mark.django_db
def test_per_user_limit_above_one_across_modes():
    world = make_world()
    coupon = _coupon(user_limit=2)
    assert _book_with(world, coupon, mode='BY_ITEM').status_code == 201
    assert _book_with(world, coupon, mode='BY_WEIGHT').status_code == 201
    assert _book_with(world, coupon, mode='BY_WEIGHT').status_code == 400
    coupon.refresh_from_db()
    assert coupon.current_usage == 2


@pytest.mark.django_db
def test_max_usage_cannot_be_bypassed_with_weight_orders():
    world = make_world()
    coupon = _coupon(max_usage=2, user_limit=5)
    for n in range(2):
        assert _book_with(world, coupon, mode='BY_WEIGHT', customer=_another_customer(n)).status_code == 201
    blocked = _book_with(world, coupon, mode='BY_WEIGHT', customer=_another_customer(9))
    assert blocked.status_code == 400
    coupon.refresh_from_db()
    assert coupon.current_usage == 2


@pytest.mark.django_db
def test_one_coupon_usage_per_order_is_enforced_by_the_database():
    from django.db import IntegrityError, transaction
    world = make_world()
    coupon = _coupon(user_limit=5)
    order = Order.objects.get(pk=_book_with(world, coupon, mode='BY_WEIGHT').data['id'])
    with pytest.raises(IntegrityError), transaction.atomic():
        CouponUsage.objects.create(user=world.customer, coupon=coupon, order=order)


@pytest.mark.django_db
@pytest.mark.parametrize('overrides,message', [
    ({'valid_to': timezone.now() - timedelta(days=1)}, 'expired'),
    ({'is_active': False}, 'inactive'),
])
@pytest.mark.parametrize('mode', ['BY_ITEM', 'BY_WEIGHT'])
def test_expired_or_disabled_coupons_are_refused(overrides, message, mode):
    world = make_world()
    coupon = _coupon(**overrides)
    response = _book_with(world, coupon, mode=mode)
    assert response.status_code == 400
    assert message in str(response.data['coupon_code']).lower()
    assert not CouponUsage.objects.filter(coupon=coupon).exists()
    assert Order.objects.filter(user=world.customer).count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != 'postgresql', reason='needs PostgreSQL row locking')
def test_concurrent_weight_redemptions_respect_max_usage():
    world = make_world()
    coupon = _coupon(max_usage=3, user_limit=1)
    customers = [_another_customer(100 + n) for n in range(12)]
    barrier = threading.Barrier(12)
    codes = []

    def run(i):
        try:
            import copy
            w = copy.copy(world)
            barrier.wait()
            codes.append(_book_with(w, coupon, mode='BY_WEIGHT', customer=customers[i]).status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    coupon.refresh_from_db()
    assert codes.count(201) == 3
    assert coupon.current_usage == 3 and CouponUsage.objects.filter(coupon=coupon).count() == 3
    assert Order.objects.filter(coupon=coupon).count() == 3
