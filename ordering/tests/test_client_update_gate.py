"""
Outdated app builds cannot book while transport is priced in the app.

Build 9 (the launch build) tells the customer transport is "not charged in the
app" and then charges it at checkout. While pricing is ON it is asked to
update before starting or placing a booking; everything else keeps working.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService
from logistics.models import LogisticsPricingConfig
from logistics.services.client_gate import APP_UPDATE_MESSAGE, APP_UPDATE_REQUIRED, parse_client
from ordering.models import LaunderableItem, Order
from users.models import User

OLD = {'HTTP_X_CLIENT_PLATFORM': 'android', 'HTTP_X_CLIENT_VERSION': '1.0.0+9'}
NEW = {'HTTP_X_CLIENT_PLATFORM': 'android', 'HTTP_X_CLIENT_VERSION': '1.0.0+10'}
FUTURE = {'HTTP_X_CLIENT_PLATFORM': 'android', 'HTTP_X_CLIENT_VERSION': '2.3.1+187'}
OLD_IOS = {'HTTP_X_CLIENT_PLATFORM': 'ios', 'HTTP_X_CLIENT_VERSION': '1.0.0+9'}
NEW_IOS = {'HTTP_X_CLIENT_PLATFORM': 'ios', 'HTTP_X_CLIENT_VERSION': '1.0.0+10'}


@pytest.fixture(autouse=True)
def zero_rated(settings):
    settings.TAX_RATE = 0.00
    settings.PLATFORM_FEE_RATE = 0.00


@pytest.fixture
def world(db):
    LogisticsPricingConfig.objects.all().delete()
    owner = User.objects.create_user(email='gate-owner@example.com', phone='233240001002', password='x', role='OWNER')
    customer = User.objects.create_user(email='gate-cust@example.com', phone='233240001001', password='x', role='CUSTOMER')
    laundry = Laundry.objects.create(
        name='Gate Laundry', owner=owner, phone_number='0240001002', address='Osu', city='Accra',
        latitude=Decimal('5.556000'), longitude=Decimal('-0.182000'),
        status=Laundry.ApprovalStatus.APPROVED, is_active=True, service_radius_km=Decimal('30'),
    )
    st = Category.objects.create(name='Gate Wash', type=Category.CategoryType.SERVICE_TYPE)
    ic = Category.objects.create(name='Gate Shirts', type=Category.CategoryType.ITEM_CATEGORY)
    item = LaunderableItem.objects.create(name='Gate Shirt', item_category=ic)
    LaundryService.objects.create(laundry=laundry, item=item, service_type=st, price='40.00', is_available=True)
    config = LogisticsPricingConfig.objects.create(
        pricing_enabled=False, is_active=True, effective_from=timezone.now() - timedelta(minutes=1),
        pickup_price_per_km=Decimal('3.00'), delivery_price_per_km=Decimal('4.00'),
    )
    client = APIClient()
    client.force_authenticate(customer)
    return {'customer': customer, 'laundry': laundry, 'item': item, 'st': st, 'config': config, 'client': client}


def pricing_on(world):
    world['config'].pricing_enabled = True
    world['config'].save()


def items(world):
    return [{'item': str(world['item'].id), 'service_type': str(world['st'].id), 'quantity': 2}]


def estimate(world, headers):
    return world['client'].post('/api/v1/booking/estimate/', {
        'laundry': str(world['laundry'].id), 'items': items(world),
        'pickup_lat': '5.5650', 'pickup_lng': '-0.1820',
    }, format='json', **headers)


def book(world, headers, method='CASH'):
    return world['client'].post('/api/v1/booking/create/', {
        'laundry': str(world['laundry'].id),
        'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
        'pickup_address': 'Pin', 'pickup_lat': '5.5650', 'pickup_lng': '-0.1820',
        'delivery_address': 'Pin', 'delivery_lat': '5.5650', 'delivery_lng': '-0.1820',
        'items': items(world), 'payment_method': method,
    }, format='json', **headers)


def assert_update_required(response):
    assert response.status_code == 426, response.data
    assert response.data['code'] == APP_UPDATE_REQUIRED
    assert response.data['message'].startswith('A newer version of Simame is required')
    assert response.data['data']['store_url'].startswith('https://')


@pytest.mark.parametrize('header, expected', [
    ('1.0.0+9', 9),
    ('1.0.0+10', 10),
    (' 2.3.1+187 ', 187),
    ('1.0.0', None),
    ('unknown', None),
    ('1.0.0+abc', None),
    ('+', None),
    ('', None),
    ('1.0.0+9+1', None),
])
def test_build_is_parsed_from_the_numeric_part_only(header, expected):
    request = type('R', (), {'META': {'HTTP_X_CLIENT_PLATFORM': 'Android', 'HTTP_X_CLIENT_VERSION': header}})()
    platform, build, _ = parse_client(request)
    assert platform == 'android'
    assert build == expected


@pytest.mark.django_db
class TestPricingOff:
    def test_old_build_books_normally(self, world):
        assert estimate(world, OLD).status_code == 200
        response = book(world, OLD)
        assert response.status_code == 201, response.data
        assert Order.objects.get(id=response.data['id']).total_amount == Decimal('80.00')

    def test_malformed_and_missing_versions_are_not_blocked(self, world):
        assert estimate(world, {'HTTP_X_CLIENT_PLATFORM': 'android'}).status_code == 200
        assert estimate(world, {'HTTP_X_CLIENT_PLATFORM': 'android', 'HTTP_X_CLIENT_VERSION': 'garbage'}).status_code == 200


@pytest.mark.django_db
class TestPricingOn:
    def test_old_android_build_cannot_quote_or_book(self, world):
        pricing_on(world)
        assert_update_required(estimate(world, OLD))
        response = book(world, OLD)
        assert_update_required(response)
        assert response.data['message'] == APP_UPDATE_MESSAGE
        assert response.data['data']['min_build'] == 10
        assert response.data['data']['client_build'] == 9
        assert 'play.google.com' in response.data['data']['store_url']
        assert Order.objects.count() == 0

    def test_old_build_is_blocked_on_every_booking_start_route(self, world):
        pricing_on(world)
        c = world['client']
        assert_update_required(c.post('/api/v1/booking/calculate/', {'laundry': str(world['laundry'].id), 'items': items(world)}, format='json', **OLD))
        assert_update_required(c.post('/api/v1/orders/', {}, format='json', **OLD))
        assert_update_required(c.post('/api/v1/logistics/quote/', {'laundry': str(world['laundry'].id)}, format='json', **OLD))

    def test_old_ios_build_is_sent_to_the_app_store(self, world):
        pricing_on(world)
        response = book(world, OLD_IOS)
        assert_update_required(response)
        assert 'apps.apple.com' in response.data['data']['store_url']
        assert 'App Store' in response.data['message']

    def test_new_build_can_quote_and_book_cash(self, world):
        pricing_on(world)
        quoted = estimate(world, NEW)
        assert quoted.status_code == 200
        assert quoted.data['data']['transport_status'] == 'PRICED'
        response = book(world, NEW, 'CASH')
        assert response.status_code == 201, response.data
        order = Order.objects.get(id=response.data['id'])
        # 80 items + 1 km x 3 + 1 km x 4
        assert order.total_amount == Decimal('87.00') == Decimal(quoted.data['data']['total'])
        assert response.data['payment_intent']['amount'] == '87.00'

    def test_new_build_can_book_with_paystack(self, world):
        pricing_on(world)
        ok = {'status': True, 'data': {'access_code': 'acc', 'authorization_url': 'https://paystack.test/x'}}
        with patch('payments.services.paystack.PaystackService.initialize_transaction', return_value=ok) as init:
            response = book(world, NEW, 'CARD')
        assert response.status_code == 201, response.data
        assert Decimal(str(init.call_args.args[1])) == Decimal('87.00')

    def test_new_ios_and_future_builds_are_allowed(self, world):
        pricing_on(world)
        assert estimate(world, NEW_IOS).status_code == 200
        assert estimate(world, FUTURE).status_code == 200

    @pytest.mark.parametrize('headers', [
        {'HTTP_X_CLIENT_PLATFORM': 'android'},                                   # missing version
        {'HTTP_X_CLIENT_PLATFORM': 'android', 'HTTP_X_CLIENT_VERSION': 'unknown'},  # malformed
        {'HTTP_X_CLIENT_PLATFORM': 'android', 'HTTP_X_CLIENT_VERSION': '1.0.0'},    # no build
        {'HTTP_X_CLIENT_VERSION': '1.0.0+10'},                                    # version, no platform
    ])
    def test_mobile_requests_that_cannot_prove_their_build_are_blocked(self, world, headers):
        pricing_on(world)
        assert_update_required(estimate(world, headers))

    @pytest.mark.parametrize('headers', [{}, {'HTTP_X_CLIENT_PLATFORM': 'web'}])
    def test_non_mobile_clients_are_not_blocked(self, world, headers):
        pricing_on(world)
        assert estimate(world, headers).status_code == 200

    def test_minimum_build_is_admin_configurable(self, world):
        pricing_on(world)
        world['config'].min_android_build = 12
        world['config'].save()
        assert_update_required(estimate(world, NEW))
        assert estimate(world, {'HTTP_X_CLIENT_PLATFORM': 'android', 'HTTP_X_CLIENT_VERSION': '1.1.0+12'}).status_code == 200

    def test_old_build_keeps_everything_that_is_not_a_new_booking(self, world):
        # An order placed before pricing was switched on.
        placed = book(world, OLD)
        assert placed.status_code == 201
        order_id = placed.data['id']
        other = book(world, OLD)
        assert other.status_code == 201
        pricing_on(world)
        c = world['client']

        assert c.get('/api/v1/orders/', **OLD).status_code == 200
        assert c.get(f'/api/v1/orders/{order_id}/', **OLD).status_code == 200
        assert c.get(f'/api/v1/orders/{order_id}/tracking/', **OLD).status_code == 200
        assert c.get(f'/api/v1/orders/{order_id}/price-breakdown/', **OLD).status_code == 200
        assert c.get('/api/v1/laundries/laundries/', **OLD).status_code == 200
        assert c.get('/api/v1/logistics/pricing/', **OLD).status_code == 200

        Order.objects.filter(id=order_id).update(status=Order.Status.OUT_FOR_DELIVERY)
        confirmed = c.post(f'/api/v1/orders/{order_id}/confirm-received/', {}, format='json', **OLD)
        assert confirmed.status_code == 200, confirmed.data

        # A paid card order, delivered, with the laundry's payment still held.
        from payments.models import Payment
        from payments.services.settlement_service import SettlementService
        delivered = Order.objects.get(id=other.data['id'])
        delivered.payment_method = Order.PaymentMethod.CARD
        delivered.payment_status = Order.PaymentStatus.PAID
        delivered.status = Order.Status.DELIVERED
        delivered.save()
        Payment.objects.create(
            user=world['customer'], order=delivered, amount=delivered.total_amount, currency='GHS',
            transaction_reference='GATE-DSP-1', payment_method=Payment.Method.CARD, status=Payment.Status.SUCCESS,
        )
        SettlementService.record_for_order(delivered)
        SettlementService.release_for_order(delivered, confirmed=False)
        dispute = c.post(f'/api/v1/orders/{delivered.id}/report-problem/',
                         {'reason': 'ITEMS_MISSING', 'details': 'One shirt missing'}, format='json', **OLD)
        assert dispute.status_code == 201, dispute.data
