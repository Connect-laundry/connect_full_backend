"""Duplicate-order and promo-redemption guards under real-world retries."""
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from ordering.models import Order
from ordering.models.coupons import Coupon, CouponUsage
from test_booking_create import _booking_payload, _build_booking_catalog


@pytest.fixture(autouse=True)
def production_idempotency(settings):
    # Test settings omit this middleware; production runs it after auth.
    settings.MIDDLEWARE = [
        *[m for m in settings.MIDDLEWARE if 'deactivation' not in m],
        'config.middleware.idempotency.IdempotencyMiddleware',
        'config.middleware.deactivation.DeactivationMiddleware',
    ]


def _jwt_client(email, **meta):
    """A client authenticated the way the app is: with a real access token."""
    client = APIClient()
    login = client.post(reverse('auth_login'), {'email': email, 'password': 'StrongPass123!'},
                        format='json', **meta)
    assert login.status_code == status.HTTP_200_OK, login.content
    token = (login.json().get('data') or login.json())['accessToken']
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


@pytest.mark.django_db
def test_retry_after_switching_wifi_to_mobile_data_does_not_duplicate_the_order():
    customer, laundry, item, service_type, _ = _build_booking_catalog('NetSwitch')
    payload = {**_booking_payload(laundry, item, service_type), 'payment_method': 'CASH'}
    client = _jwt_client(customer.email)

    first = client.post(reverse('booking-create'), payload, format='json',
                        HTTP_X_IDEMPOTENCY_KEY='place-order-1',
                        REMOTE_ADDR='41.66.200.10', HTTP_X_FORWARDED_FOR='41.66.200.10, 104.22.160.34, 10.198.1.1')
    # Production has no Redis: the view-level idempotency cache is per worker
    # process, so the retry landing on another worker (or after a restart)
    # only has the database-backed middleware record to catch it.
    from django.core.cache import cache
    cache.clear()
    # Same tap, retried after the phone moved to mobile data: new IP, new chain.
    retry = client.post(reverse('booking-create'), payload, format='json',
                        HTTP_X_IDEMPOTENCY_KEY='place-order-1',
                        REMOTE_ADDR='154.160.3.4', HTTP_X_FORWARDED_FOR='154.160.3.4, 172.70.9.9, 10.197.2.2')

    assert first.status_code == status.HTTP_201_CREATED, first.content
    assert retry.status_code == status.HTTP_201_CREATED
    assert Order.objects.filter(user=customer).count() == 1
    assert (retry.json().get('data') or retry.json())['id'] == (first.json().get('data') or first.json())['id']


@pytest.mark.django_db
def test_two_customers_reusing_one_idempotency_key_do_not_collide():
    customer, laundry, item, service_type, _ = _build_booking_catalog('KeyScope')
    from users.models import User
    other = User.objects.create_user(email='keyscope-other@example.com', phone='233555901099',
                                     password='StrongPass123!')
    payload = {**_booking_payload(laundry, item, service_type), 'payment_method': 'CASH'}
    for user in (customer, other):
        response = _jwt_client(user.email).post(reverse('booking-create'), payload, format='json',
                                                HTTP_X_IDEMPOTENCY_KEY='shared-key', REMOTE_ADDR='41.66.200.10')
        assert response.status_code == status.HTTP_201_CREATED, response.content
    assert Order.objects.filter(user=customer).count() == 1
    assert Order.objects.filter(user=other).count() == 1


@pytest.mark.django_db
def test_one_per_customer_coupon_cannot_be_redeemed_twice_by_racing_orders():
    customer, laundry, item, service_type, _ = _build_booking_catalog('CouponRace')
    Coupon.objects.create(code='LAUNCH50', discount_type='FIXED', discount_value='5.00',
                          user_limit=1, is_active=True)
    payload = {**_booking_payload(laundry, item, service_type), 'payment_method': 'CASH', 'coupon_code': 'LAUNCH50'}
    client = APIClient()
    client.force_authenticate(customer)

    assert client.post(reverse('booking-create'), payload, format='json').status_code == status.HTTP_201_CREATED
    # Simulate the second order passing the unlocked pre-check at the same moment.
    with patch.object(Coupon, 'is_valid', return_value=(True, '')):
        second = client.post(reverse('booking-create'), payload, format='json')

    assert second.status_code == status.HTTP_400_BAD_REQUEST
    assert CouponUsage.objects.filter(user=customer, coupon__code='LAUNCH50').count() == 1
