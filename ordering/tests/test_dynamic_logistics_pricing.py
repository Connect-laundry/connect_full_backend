"""
Dynamic pickup & delivery pricing, end to end.

Admin-controlled rates -> server quote -> checkout estimate -> order snapshot
-> Paystack amount / cash due -> settlement and split routing -> promo push.

The mobile app only renders what these endpoints return, so every behaviour a
customer sees without an app release is pinned here.
"""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.favorite import Favorite
from laundries.models.laundry import Laundry, OwnerAuditLog
from laundries.models.service import LaundryService
from laundries.services import promo_notifications
from logistics.admin import LogisticsPricingConfigAdmin
from logistics.models import LogisticsPricingAudit, LogisticsPricingConfig
from logistics.services.pricing_service import (
    TEMPORARY_LOGISTICS_NOTICE,
    LogisticsPricingService,
    TransportStatus,
)
from marketplace.models import Notification, NotificationPreference
from ordering.models import LaunderableItem, Order, OrderItem
from ordering.services.finance_service import FinanceService
from payments.services.settlement_service import SettlementService
from payments.services.split_routing import platform_charge_pesewas
from users.models import User

# Laundry pin. One degree of latitude is 111.19493 km on the 6371 km sphere
# the service uses, so these offsets sit exactly on the test distances.
LAUNDRY_LAT = Decimal('5.600000')
LAUNDRY_LNG = Decimal('-0.180000')
DEG_PER_KM = Decimal('1') / Decimal('111.19492664')


def lat_at(km):
    return (LAUNDRY_LAT + Decimal(str(km)) * DEG_PER_KM).quantize(Decimal('0.0000001'))


@pytest.fixture(autouse=True)
def production_fee_rates(settings):
    """Production charges no commission or VAT; tests that need one set it."""
    settings.TAX_RATE = 0.00
    settings.PLATFORM_FEE_RATE = 0.00
    settings.PAYSTACK_SPLIT_ENABLED = True
    return settings


@pytest.fixture
def owner(db):
    return User.objects.create_user(
        email='logistics-owner@example.com', phone='233240000002', password='pw-Strong-123', role='OWNER',
    )


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        email='logistics-customer@example.com', phone='233240000001', password='pw-Strong-123', role='CUSTOMER',
    )


@pytest.fixture
def laundry(owner):
    return Laundry.objects.create(
        name='Adepa Laundry', owner=owner, phone_number='+233240000002',
        address='Airport Residential, Accra', city='Accra',
        latitude=LAUNDRY_LAT, longitude=LAUNDRY_LNG,
        status=Laundry.ApprovalStatus.APPROVED, is_active=True,
        service_radius_km=Decimal('30.00'),
    )


@pytest.fixture
def catalog(laundry):
    service_type = Category.objects.create(name='Wash', type=Category.CategoryType.SERVICE_TYPE)
    item_category = Category.objects.create(name='Shirts', type=Category.CategoryType.ITEM_CATEGORY)
    item = LaunderableItem.objects.create(name='Shirt', item_category=item_category)
    LaundryService.objects.create(
        laundry=laundry, item=item, service_type=service_type, price='40.00', is_available=True,
    )
    return item, service_type


@pytest.fixture
def pricing(db):
    """Launch rates: pickup GHS 3/km, delivery GHS 4/km, nothing else."""
    # Replaces the disabled row the migration seeds on a fresh database.
    LogisticsPricingConfig.objects.all().delete()
    return LogisticsPricingConfig.objects.create(
        pricing_enabled=True, is_active=True, effective_from=timezone.now() - timedelta(minutes=5),
        pickup_price_per_km=Decimal('3.00'), delivery_price_per_km=Decimal('4.00'),
        pickup_base_fee=Decimal('0'), delivery_base_fee=Decimal('0'),
        pickup_min_fee=Decimal('0'), delivery_min_fee=Decimal('0'),
        max_service_distance_km=Decimal('25.00'), distance_rounding_precision=1,
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def estimate(client, laundry, catalog, pickup=(None, None), delivery=(None, None), quantity=2):
    item, service_type = catalog
    body = {
        'laundry': str(laundry.id),
        'items': [{'item': str(item.id), 'service_type': str(service_type.id), 'quantity': quantity}],
    }
    if pickup[0] is not None:
        body.update(pickup_lat=str(pickup[0]), pickup_lng=str(pickup[1]))
    if delivery[0] is not None:
        body.update(delivery_lat=str(delivery[0]), delivery_lng=str(delivery[1]))
    response = client.post('/api/v1/booking/estimate/', body, format='json')
    assert response.status_code == 200, response.data
    return response.data['data']


def booking_body(laundry, catalog, pickup_lat, delivery_lat=None, method='CARD', quantity=2, **extra):
    item, service_type = catalog
    delivery_lat = pickup_lat if delivery_lat is None else delivery_lat
    body = {
        'laundry': str(laundry.id),
        'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
        'pickup_address': 'East Legon pickup',
        'pickup_lat': str(pickup_lat), 'pickup_lng': str(LAUNDRY_LNG),
        'delivery_address': 'East Legon pickup' if delivery_lat == pickup_lat else 'Osu delivery',
        'delivery_lat': str(delivery_lat), 'delivery_lng': str(LAUNDRY_LNG),
        'items': [{'item': str(item.id), 'service_type': str(service_type.id), 'quantity': quantity}],
        'payment_method': method,
    }
    body.update(extra)
    return body


def paystack_ok():
    return {'status': True, 'data': {'access_code': 'acc_test', 'authorization_url': 'https://paystack.test/x'}}


# --------------------------------------------------------------------------
# 1. Pricing disabled: the temporary "not included" notice
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestPricingDisabled:
    @pytest.fixture(autouse=True)
    def no_enabled_rows(self, db):
        LogisticsPricingConfig.objects.filter(pricing_enabled=True).delete()

    def test_estimate_says_transport_is_not_included(self, customer, laundry, catalog):
        data = estimate(client_for(customer), laundry, catalog, pickup=(lat_at(2), LAUNDRY_LNG))

        assert data['transport_status'] == TransportStatus.NOT_INCLUDED
        assert data['pricing_enabled'] is False
        assert data['delivery_fees_in_app'] is False
        assert data['logistics_notice'] == TEMPORARY_LOGISTICS_NOTICE
        assert data['pickup_fee'] == data['delivery_fee'] == '0.00'
        # The total is the items only, and is not dressed up as including transport.
        assert data['items_total'] == data['total'] == '80.00'

    def test_seeded_row_is_disabled(self, db):
        # The migration seeds one disabled row for the admin to edit.
        config = LogisticsPricingConfig(pricing_enabled=False, effective_from=timezone.now())
        config.save()
        quote = LogisticsPricingService.calculate_quote(None, lat_at(1), LAUNDRY_LNG)
        assert quote['transport_status'] == TransportStatus.NOT_INCLUDED

    def test_cash_order_does_not_pretend_transport_was_included(self, customer, laundry, catalog):
        response = client_for(customer).post(
            '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at(2), method='CASH'), format='json',
        )
        assert response.status_code == 201, response.data
        order = Order.objects.get(id=response.data['id'])
        assert order.total_amount == Decimal('80.00')
        assert order.delivery_fees_in_app is False
        assert order.logistics_notice == TEMPORARY_LOGISTICS_NOTICE
        assert response.data['payment_intent']['amount'] == '80.00'

    def test_future_rates_are_not_used_early(self, laundry):
        LogisticsPricingConfig.objects.create(
            pricing_enabled=True, is_active=True, effective_from=timezone.now() + timedelta(days=1),
            pickup_price_per_km=Decimal('3'), delivery_price_per_km=Decimal('4'),
        )
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(1), LAUNDRY_LNG)
        assert quote['pricing_enabled'] is False


# --------------------------------------------------------------------------
# 2. Per-km pricing, both legs, distance rules and money precision
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestPerKmPricing:
    @pytest.mark.parametrize('km, pickup, delivery', [
        ('0.5', '1.50', '2.00'),
        ('1', '3.00', '4.00'),
        ('1.5', '4.50', '6.00'),
        ('10', '30.00', '40.00'),
    ])
    def test_distance_times_rate(self, laundry, pricing, km, pickup, delivery):
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(km), LAUNDRY_LNG)
        assert quote['pickup_distance_km'] == Decimal(km).quantize(Decimal('0.1'))
        assert quote['delivery_distance_km'] == Decimal(km).quantize(Decimal('0.1'))
        assert quote['pickup_fee'] == Decimal(pickup)
        assert quote['delivery_fee'] == Decimal(delivery)
        assert quote['total_logistics_fee'] == Decimal(pickup) + Decimal(delivery)
        assert quote['transport_status'] == TransportStatus.PRICED
        assert quote['logistics_notice'] == ''

    def test_pickup_and_delivery_legs_are_measured_separately(self, laundry, pricing):
        # Pickup 2 km north, delivery 5 km south.
        south = (LAUNDRY_LAT - Decimal('5') * DEG_PER_KM).quantize(Decimal('0.0000001'))
        quote = LogisticsPricingService.calculate_quote(
            laundry, lat_at(2), LAUNDRY_LNG, delivery_lat=south, delivery_lng=LAUNDRY_LNG,
        )
        assert quote['pickup_distance_km'] == Decimal('2.0')
        assert quote['delivery_distance_km'] == Decimal('5.0')
        assert quote['pickup_fee'] == Decimal('6.00')
        assert quote['delivery_fee'] == Decimal('20.00')

    def test_maximum_radius_is_bookable(self, laundry, pricing):
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(25), LAUNDRY_LNG)
        assert quote['quote_available'] is True
        assert quote['pickup_fee'] == Decimal('75.00')

    def test_outside_maximum_radius_is_unavailable(self, laundry, pricing):
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(26), LAUNDRY_LNG)
        assert quote['quote_available'] is False
        assert quote['transport_status'] == TransportStatus.UNAVAILABLE
        assert quote['outside_service_area'] is True
        assert quote['pickup_fee'] == quote['delivery_fee'] == Decimal('0.00')
        assert '25.0 km' in quote['unavailable_reason']

    def test_laundry_service_radius_still_applies(self, laundry, pricing):
        laundry.service_radius_km = Decimal('5.00')
        laundry.save()
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(6), LAUNDRY_LNG)
        assert quote['quote_available'] is False
        assert 'Adepa Laundry' in quote['unavailable_reason']

    def test_base_fee_minimum_fee_minimum_distance_and_road_factor(self, laundry, pricing):
        pricing.pickup_base_fee = Decimal('5.00')
        pricing.delivery_min_fee = Decimal('10.00')
        pricing.minimum_billable_distance_km = Decimal('1.00')
        pricing.road_distance_factor = Decimal('1.30')
        pricing.save()

        near = LogisticsPricingService.calculate_quote(laundry, lat_at('0.3'), LAUNDRY_LNG)
        # 0.3 km x 1.3 = 0.39 -> 0.4 km, raised to the 1 km billable minimum.
        assert near['pickup_distance_km'] == Decimal('1.00')
        assert near['pickup_fee'] == Decimal('8.00')      # 5 + 1 x 3
        assert near['delivery_fee'] == Decimal('10.00')   # 1 x 4 = 4, minimum 10

        far = LogisticsPricingService.calculate_quote(laundry, lat_at(10), LAUNDRY_LNG)
        assert far['pickup_distance_km'] == Decimal('13.0')  # 10 km x 1.3
        assert far['pickup_fee'] == Decimal('44.00')       # 5 + 13 x 3

    def test_money_is_decimal_and_rounded_half_up(self, laundry, pricing):
        pricing.pickup_price_per_km = Decimal('2.35')
        pricing.distance_rounding_precision = 2
        pricing.save()
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at('1.3'), LAUNDRY_LNG)
        assert isinstance(quote['pickup_fee'], Decimal)
        assert quote['pickup_distance_km'] == Decimal('1.30')
        assert quote['pickup_fee'] == Decimal('3.06')  # 3.055 rounds half up

    @pytest.mark.parametrize('lat, lng', [(None, None), ('0', '0'), ('95', '-0.18'), ('abc', '-0.18')])
    def test_missing_or_invalid_pin_never_invents_a_price(self, laundry, pricing, lat, lng):
        quote = LogisticsPricingService.calculate_quote(laundry, lat, lng)
        assert quote['quote_available'] is False
        assert quote['transport_status'] == TransportStatus.UNAVAILABLE
        assert quote['total_logistics_fee'] == Decimal('0.00')

    def test_laundry_without_a_pin_cannot_be_priced(self, laundry, pricing):
        laundry.latitude = laundry.longitude = None
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(1), LAUNDRY_LNG)
        assert quote['quote_available'] is False


# --------------------------------------------------------------------------
# 3. Admin changes rates: live apps get them on the next request
# --------------------------------------------------------------------------

def admin_save(config, changes, user):
    """Drive LogisticsPricingConfigAdmin.save_model as the admin change form does."""
    from django.contrib import admin as django_admin
    model_admin = LogisticsPricingConfigAdmin(LogisticsPricingConfig, django_admin.site)
    initial = {field: getattr(config, field) for field in changes}
    for field, value in changes.items():
        setattr(config, field, value)
    form = SimpleNamespace(initial=initial, cleaned_data=dict(changes), changed_data=list(changes))
    request = RequestFactory().post('/admin/')
    request.user = user
    model_admin.save_model(request, config, form, change=True)


@pytest.mark.django_db
class TestAdminRateChangeWithoutRelease:
    def test_new_rates_reach_the_next_quote_and_old_orders_keep_theirs(
        self, customer, laundry, catalog, pricing,
    ):
        admin_user = User.objects.create_superuser(
            email='rates-admin@example.com', phone='233240000099', password='pw-Strong-123',
        )
        client = client_for(customer)

        before = estimate(client, laundry, catalog, pickup=(lat_at(2), LAUNDRY_LNG))
        assert (before['pickup_fee'], before['delivery_fee'], before['total']) == ('6.00', '8.00', '94.00')
        with patch('payments.services.paystack.PaystackService.initialize_transaction', return_value=paystack_ok()):
            placed = client.post('/api/v1/booking/create/', booking_body(laundry, catalog, lat_at(2)), format='json')
        assert placed.status_code == 201, placed.data
        old_order = Order.objects.get(id=placed.data['id'])
        old_version = old_order.logistics_pricing_version

        # Two weeks later: pickup 3 -> 4 per km, delivery 4 -> 5 per km. No app release.
        admin_save(pricing, {'pickup_price_per_km': Decimal('4.00'), 'delivery_price_per_km': Decimal('5.00')}, admin_user)

        after = estimate(client, laundry, catalog, pickup=(lat_at(2), LAUNDRY_LNG))
        assert (after['pickup_fee'], after['delivery_fee'], after['total']) == ('8.00', '10.00', '98.00')
        assert after['pricing_version'] != before['pricing_version']

        public = APIClient().get('/api/v1/logistics/pricing/').data['data']
        assert (public['pickup_rate_per_km'], public['delivery_rate_per_km']) == ('4.00', '5.00')

        old_order.refresh_from_db()
        assert old_order.pickup_rate_per_km == Decimal('3.00')
        assert old_order.total_amount == Decimal('94.00')
        assert old_order.logistics_pricing_version == old_version
        assert FinanceService.calculate_price_breakdown(old_order)['total'] == '94.00'

        audit = LogisticsPricingAudit.objects.filter(config=pricing).latest('changed_at')
        assert audit.changed_by == admin_user
        assert audit.old_values == {'pickup_price_per_km': '3.00', 'delivery_price_per_km': '4.00'}
        assert audit.new_values == {'pickup_price_per_km': '4.00', 'delivery_price_per_km': '5.00'}

    def test_switching_pricing_on_removes_the_notice_everywhere(self, customer, laundry, catalog):
        LogisticsPricingConfig.objects.all().delete()
        config = LogisticsPricingConfig.objects.create(
            pricing_enabled=False, is_active=True, effective_from=timezone.now() - timedelta(minutes=1),
            pickup_price_per_km=Decimal('3'), delivery_price_per_km=Decimal('4'),
        )
        client = client_for(customer)
        detail = client.get(f'/api/v1/laundries/laundries/{laundry.id}/').data
        detail = detail.get('data', detail)
        assert detail['logistics']['transport_status'] == TransportStatus.NOT_INCLUDED
        assert detail['logistics']['notice'] == TEMPORARY_LOGISTICS_NOTICE

        config.pricing_enabled = True
        config.save()

        detail = client.get(f'/api/v1/laundries/laundries/{laundry.id}/').data
        detail = detail.get('data', detail)
        assert detail['logistics']['transport_status'] == TransportStatus.PRICED
        assert detail['logistics']['notice'] == ''
        assert detail['logistics']['pickup_rate_per_km'] == '3.00'
        data = estimate(client, laundry, catalog, pickup=(lat_at(1), LAUNDRY_LNG))
        assert data['logistics_notice'] == ''
        assert data['total'] == '87.00'

    def test_version_numbers_are_global(self, pricing):
        second = LogisticsPricingConfig.objects.create(
            pricing_enabled=True, effective_from=timezone.now(),
        )
        assert second.version == pricing.version + 1

    def test_invalid_admin_values_are_rejected(self, pricing):
        from django.core.exceptions import ValidationError
        pricing.pickup_price_per_km = Decimal('-1')
        with pytest.raises(ValidationError):
            pricing.full_clean()


# --------------------------------------------------------------------------
# 4. Paystack and cash use the same authoritative grand total
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestGrandTotals:
    def test_paystack_is_charged_items_plus_pickup_plus_delivery(self, customer, laundry, catalog, pricing):
        with patch('payments.services.paystack.PaystackService.initialize_transaction',
                   return_value=paystack_ok()) as init:
            response = client_for(customer).post(
                '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at('2.4')), format='json',
            )
        assert response.status_code == 201, response.data
        order = Order.objects.get(id=response.data['id'])
        # 80 items + 2.4 km x 3 + 2.4 km x 4
        assert order.items_total == Decimal('80.00')
        assert order.pickup_fee == Decimal('7.20')
        assert order.delivery_fee == Decimal('9.60')
        assert order.total_amount == Decimal('96.80')
        amount = init.call_args.args[1] if len(init.call_args.args) > 1 else init.call_args.kwargs['amount']
        assert Decimal(str(amount)) == Decimal('96.80')
        assert response.data['payment_intent']['amount'] == '96.80'
        # Immutable snapshot for accounting.
        assert order.pickup_distance_km == order.delivery_distance_km == Decimal('2.4')
        assert (order.pickup_rate_per_km, order.delivery_rate_per_km) == (Decimal('3.00'), Decimal('4.00'))
        assert order.logistics_nominal_total == Decimal('16.80')
        assert order.logistics_pricing_version == f'v{pricing.version}'

    def test_cash_on_delivery_stores_the_same_total(self, customer, laundry, catalog, pricing):
        response = client_for(customer).post(
            '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at('2.4'), method='CASH'), format='json',
        )
        assert response.status_code == 201, response.data
        order = Order.objects.get(id=response.data['id'])
        assert order.payment_method == Order.PaymentMethod.CASH
        assert order.total_amount == Decimal('96.80')
        assert response.data['payment_intent']['status'] == 'CASH_DUE'
        assert response.data['payment_intent']['amount'] == '96.80'
        breakdown = response.data['price_breakdown']
        assert (breakdown['pickup_fee'], breakdown['delivery_fee'], breakdown['total']) == ('7.20', '9.60', '96.80')

    def test_estimate_total_equals_charged_total(self, customer, laundry, catalog, pricing):
        client = client_for(customer)
        quoted = estimate(client, laundry, catalog, pickup=(lat_at('3.7'), LAUNDRY_LNG))
        response = client.post('/api/v1/booking/create/', booking_body(
            laundry, catalog, lat_at('3.7'), method='CASH', expected_total=quoted['total'],
        ), format='json')
        assert response.status_code == 201, response.data
        assert str(Order.objects.get(id=response.data['id']).total_amount) == quoted['total']

    def test_admin_notification_carries_the_breakdown(
        self, customer, laundry, catalog, pricing, django_capture_on_commit_callbacks,
    ):
        User.objects.create_user(email='ops@example.com', phone='233240000050', password='x', role='ADMIN')
        with django_capture_on_commit_callbacks(execute=True):
            response = client_for(customer).post(
                '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at('2.4'), method='CASH'), format='json',
            )
        assert response.status_code == 201
        note = Notification.objects.get(audience=Notification.Audience.ADMIN, category='NEW_BOOKING')
        for text in ('Items: GHS 80.00', 'Pickup: GHS 7.20 (2.40 km)', 'Delivery: GHS 9.60 (2.40 km)',
                     'Grand total: GHS 96.80', 'Cash'):
            assert text in note.body, note.body


# --------------------------------------------------------------------------
# 5. The customer cannot move money
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestNoClientTampering:
    def test_client_fee_and_distance_fields_are_refused(self, customer, laundry, catalog, pricing):
        body = booking_body(laundry, catalog, lat_at(5), method='CASH', pickup_fee='0.00',
                            delivery_fee='0.00', pickup_distance_km='0.1')
        response = client_for(customer).post('/api/v1/booking/create/', body, format='json')
        assert response.status_code == 400
        assert Order.objects.count() == 0

    def test_lowered_expected_total_is_rejected_as_stale(self, customer, laundry, catalog, pricing):
        body = booking_body(laundry, catalog, lat_at(5), method='CASH', expected_total='80.00')
        response = client_for(customer).post('/api/v1/booking/create/', body, format='json')
        assert response.status_code == 400
        assert response.data['code'] == 'STALE_QUOTE'
        assert response.data['updated_total'] == '115.00'
        assert Order.objects.count() == 0

    def test_booking_without_pins_is_refused_while_pricing_is_on(self, customer, laundry, catalog, pricing):
        body = booking_body(laundry, catalog, lat_at(2), method='CASH')
        for key in ('pickup_lat', 'pickup_lng', 'delivery_lat', 'delivery_lng'):
            body.pop(key)
        response = client_for(customer).post('/api/v1/booking/create/', body, format='json')
        assert response.status_code == 400
        assert response.data['code'] == 'LOGISTICS_QUOTE_UNAVAILABLE'
        assert Order.objects.count() == 0

    def test_different_delivery_address_needs_its_own_pin(self, customer, laundry, catalog, pricing):
        body = booking_body(laundry, catalog, lat_at(2), method='CASH', delivery_address='Somewhere else')
        body.pop('delivery_lat')
        body.pop('delivery_lng')
        response = client_for(customer).post('/api/v1/booking/create/', body, format='json')
        assert response.status_code == 400
        assert response.data['code'] == 'LOGISTICS_QUOTE_UNAVAILABLE'

    def test_booking_beyond_the_radius_is_refused(self, customer, laundry, catalog, pricing):
        response = client_for(customer).post(
            '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at(27), method='CASH'), format='json',
        )
        assert response.status_code == 400
        assert response.data['code'] == 'LOGISTICS_QUOTE_UNAVAILABLE'

    def test_changed_address_is_requoted(self, customer, laundry, catalog, pricing):
        client = client_for(customer)
        first = estimate(client, laundry, catalog, pickup=(lat_at(1), LAUNDRY_LNG))
        moved = estimate(client, laundry, catalog, pickup=(lat_at(4), LAUNDRY_LNG))
        assert (first['pickup_fee'], moved['pickup_fee']) == ('3.00', '12.00')
        # Placing the order with the first quote's total after moving is stale.
        response = client.post('/api/v1/booking/create/', booking_body(
            laundry, catalog, lat_at(4), method='CASH', expected_total=first['total'],
        ), format='json')
        assert response.status_code == 400
        assert response.data['code'] == 'STALE_QUOTE'

    def test_orders_placed_around_a_rate_change_each_keep_their_own_rates(self, customer, laundry, catalog, pricing):
        client = client_for(customer)
        first = client.post('/api/v1/booking/create/', booking_body(laundry, catalog, lat_at(2), method='CASH'), format='json')
        pricing.pickup_price_per_km = Decimal('10.00')
        pricing.save()
        second = client.post('/api/v1/booking/create/', booking_body(laundry, catalog, lat_at(2), method='CASH'), format='json')
        a, b = Order.objects.get(id=first.data['id']), Order.objects.get(id=second.data['id'])
        assert (a.pickup_fee, b.pickup_fee) == (Decimal('6.00'), Decimal('20.00'))
        assert a.logistics_pricing_version == b.logistics_pricing_version  # same row, snapshot differs
        assert a.pickup_rate_per_km != b.pickup_rate_per_km


# --------------------------------------------------------------------------
# 6. Free pickup / delivery promos, who funds them, and the rider
# --------------------------------------------------------------------------

def start_promo(laundry, scope=Laundry.PromoScope.PICKUP_AND_DELIVERY, funded_by='LAUNDRY', **extra):
    laundry.free_delivery_promo_enabled = True
    laundry.promo_scope = scope
    laundry.promo_funding_source = funded_by
    for key, value in extra.items():
        setattr(laundry, key, value)
    laundry.save()
    return laundry


@pytest.mark.django_db
class TestPromos:
    def test_free_pickup_and_delivery(self, customer, laundry, catalog, pricing):
        start_promo(laundry)
        data = estimate(client_for(customer), laundry, catalog, pickup=(lat_at('2.4'), LAUNDRY_LNG))
        assert data['transport_status'] == TransportStatus.FREE_PROMO
        assert (data['pickup_fee'], data['delivery_fee']) == ('0.00', '0.00')
        assert data['promo_label'] == 'FREE PICKUP & DELIVERY'
        assert data['logistics_discount'] == '16.80'
        assert data['promo_message'] == 'You saved GHS 16.80 on transport.'
        assert data['logistics_notice'] == ''
        assert data['total'] == '80.00'

    def test_free_pickup_only(self, laundry, pricing):
        start_promo(laundry, scope=Laundry.PromoScope.PICKUP_ONLY)
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(2), LAUNDRY_LNG, items_total=80)
        assert (quote['pickup_fee'], quote['delivery_fee']) == (Decimal('0.00'), Decimal('8.00'))
        assert quote['free_pickup'] is True and quote['free_delivery'] is False
        assert quote['logistics_discount'] == Decimal('6.00')
        assert quote['transport_status'] == TransportStatus.PRICED

    def test_free_delivery_only(self, laundry, pricing):
        start_promo(laundry, scope=Laundry.PromoScope.DELIVERY_ONLY)
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(2), LAUNDRY_LNG, items_total=80)
        assert (quote['pickup_fee'], quote['delivery_fee']) == (Decimal('6.00'), Decimal('0.00'))
        assert quote['promo_label'] == 'FREE DELIVERY'

    def test_expired_promo_restores_normal_pricing(self, laundry, pricing):
        start_promo(laundry, promo_start_at=timezone.now() - timedelta(days=3),
                    promo_end_at=timezone.now() - timedelta(minutes=1))
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(2), LAUNDRY_LNG, items_total=80)
        assert quote['is_promo_free_delivery'] is False
        assert quote['total_logistics_fee'] == Decimal('14.00')

    def test_minimum_order_and_distance_limits(self, laundry, pricing):
        start_promo(laundry, promo_min_order_value=Decimal('100.00'), promo_max_distance_km=Decimal('3.00'))
        small = LogisticsPricingService.calculate_quote(laundry, lat_at(2), LAUNDRY_LNG, items_total=80)
        far = LogisticsPricingService.calculate_quote(laundry, lat_at(4), LAUNDRY_LNG, items_total=150)
        ok = LogisticsPricingService.calculate_quote(laundry, lat_at(2), LAUNDRY_LNG, items_total=150)
        assert small['is_promo_free_delivery'] is False
        assert far['is_promo_free_delivery'] is False
        assert ok['is_promo_free_delivery'] is True

    def test_promo_while_pricing_is_off_replaces_the_notice(self, customer, laundry, catalog):
        LogisticsPricingConfig.objects.filter(pricing_enabled=True).delete()
        start_promo(laundry)
        data = estimate(client_for(customer), laundry, catalog, pickup=(lat_at(2), LAUNDRY_LNG))
        assert data['transport_status'] == TransportStatus.FREE_PROMO
        assert data['logistics_notice'] == ''
        assert data['promo_label'] == 'FREE PICKUP & DELIVERY'
        detail = client_for(customer).get(f'/api/v1/laundries/laundries/{laundry.id}/').data
        detail = detail.get('data', detail)
        assert detail['logistics']['promo']['label'] == 'FREE PICKUP & DELIVERY'
        assert detail['logistics']['notice'] == ''

    def test_partial_promo_while_pricing_is_off_keeps_a_notice_for_the_other_leg(self, laundry):
        start_promo(laundry, scope=Laundry.PromoScope.PICKUP_ONLY)
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(2), LAUNDRY_LNG)
        assert quote['transport_status'] == TransportStatus.NOT_INCLUDED
        assert quote['logistics_notice'].startswith('Pickup is free.')

    def test_laundry_funded_rider_is_still_paid_from_the_laundry_share(self, customer, laundry, catalog, pricing):
        start_promo(laundry, funded_by='LAUNDRY')
        laundry.split_payments_enabled = True
        laundry.paystack_subaccount_code = 'ACCT_laundry'
        laundry.save()
        with patch('payments.services.paystack.PaystackService.initialize_transaction',
                   return_value=paystack_ok()) as init:
            response = client_for(customer).post(
                '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at('2.4')), format='json',
            )
        order = Order.objects.get(id=response.data['id'])
        assert order.total_amount == Decimal('80.00')          # customer pays no transport
        assert order.logistics_nominal_total == Decimal('16.80')  # rider still earns this
        assert order.logistics_discount == Decimal('16.80')
        # Paystack keeps the rider's GHS 16.80 on the platform side of the split.
        assert init.call_args.kwargs['transaction_charge'] == 1680
        settlement = SettlementService.record_for_order(order)
        assert settlement.logistics_subsidy_deducted == Decimal('16.80')
        assert settlement.net_payable == Decimal('63.20')

    def test_simame_funded_promo_is_a_platform_cost(self, laundry, catalog, customer, pricing):
        start_promo(laundry, funded_by='SIMAME')
        response = client_for(customer).post(
            '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at('2.4'), method='CASH'), format='json',
        )
        order = Order.objects.get(id=response.data['id'])
        assert order.promo_funding_source == 'SIMAME'
        assert order.logistics_nominal_total == Decimal('16.80')
        settlement = SettlementService.record_for_order(order)
        assert settlement.logistics_subsidy_deducted == Decimal('0.00')
        assert settlement.net_payable == Decimal('80.00')

    def test_laundry_cannot_fund_more_than_the_order_is_worth(self, laundry, pricing):
        start_promo(laundry, funded_by='LAUNDRY')
        quote = LogisticsPricingService.calculate_quote(laundry, lat_at(10), LAUNDRY_LNG, items_total=Decimal('20.00'))
        # GHS 70 of transport on a GHS 20 order: the rider would go unpaid.
        assert quote['is_promo_free_delivery'] is False
        assert quote['total_logistics_fee'] == Decimal('70.00')


# --------------------------------------------------------------------------
# 7. Settlement and split routing keep the rider's money out of the laundry's
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestRiderMoneyRouting:
    def test_customer_paid_transport_is_not_laundry_money(self, customer, laundry, catalog, pricing, settings):
        settings.PLATFORM_FEE_RATE = 0.05
        response = client_for(customer).post(
            '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at('2.4'), method='CASH'), format='json',
        )
        order = Order.objects.get(id=response.data['id'])
        # 80 items + 4.00 fee + 16.80 transport
        assert order.total_amount == Decimal('100.80')
        settlement = SettlementService.record_for_order(order)
        assert settlement.gross_amount == Decimal('84.00')
        assert settlement.net_payable == Decimal('80.00')
        # On a split charge the platform keeps commission + transport.
        assert platform_charge_pesewas(order) == 400 + 1680

    def test_pricing_off_changes_nothing_for_settlements(self, customer, laundry, catalog):
        response = client_for(customer).post(
            '/api/v1/booking/create/', booking_body(laundry, catalog, lat_at(2), method='CASH'), format='json',
        )
        order = Order.objects.get(id=response.data['id'])
        assert platform_charge_pesewas(order) == 0
        assert SettlementService.record_for_order(order).net_payable == Decimal('80.00')


# --------------------------------------------------------------------------
# 8. Owner promo control and the customer push
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestOwnerPromoAndPush:
    @pytest.fixture
    def audience(self, laundry, customer):
        fan = User.objects.create_user(email='fan@example.com', phone='233240000011', password='x', role='CUSTOMER')
        Favorite.objects.create(user=fan, laundry=laundry)
        opted_out = User.objects.create_user(email='quiet@example.com', phone='233240000012', password='x', role='CUSTOMER')
        Favorite.objects.create(user=opted_out, laundry=laundry)
        NotificationPreference.objects.update_or_create(user=opted_out, defaults={'promotions': False})
        stranger = User.objects.create_user(email='stranger@example.com', phone='233240000013', password='x', role='CUSTOMER')
        Order.objects.create(user=customer, laundry=laundry, pickup_date=timezone.now(), total_amount=10)
        return SimpleNamespace(fan=fan, opted_out=opted_out, stranger=stranger, past=customer)

    def put_promo(self, owner, body, capture):
        with capture(execute=True):
            return client_for(owner).put('/api/v1/laundries/dashboard/my-laundry/promotion/', body, format='json')

    def promo_notes(self):
        return Notification.objects.filter(audience=Notification.Audience.USER, type=Notification.Type.PROMO)

    def test_owner_turns_on_free_delivery_and_relevant_customers_are_told_once(
        self, owner, laundry, audience, django_capture_on_commit_callbacks,
    ):
        response = self.put_promo(owner, {'enabled': True, 'scope': 'PICKUP_AND_DELIVERY', 'name': 'Opening week'},
                                  django_capture_on_commit_callbacks)
        assert response.status_code == 200, response.data
        assert response.data['data']['status'] == 'RUNNING'
        assert response.data['data']['funded_by'] == 'LAUNDRY'

        notified = set(self.promo_notes().values_list('user_id', flat=True))
        assert notified == {audience.fan.id, audience.past.id}
        note = self.promo_notes().first()
        assert note.title == 'Adepa Laundry now has free pickup & delivery \U0001F389'
        assert note.action_url == f'/laundry-details?id={laundry.id}'
        assert note.category == 'PROMO'

        # Editing the running promo does not notify anyone again.
        self.put_promo(owner, {'name': 'Opening fortnight', 'end_at': None}, django_capture_on_commit_callbacks)
        assert self.promo_notes().count() == 2
        # Neither does switching it off and on again inside the cooldown.
        self.put_promo(owner, {'enabled': False}, django_capture_on_commit_callbacks)
        self.put_promo(owner, {'enabled': True}, django_capture_on_commit_callbacks)
        assert self.promo_notes().count() == 2
        assert OwnerAuditLog.objects.filter(laundry=laundry, action='UPDATE_PROMOTION').count() == 4

    def test_a_genuinely_new_campaign_after_the_cooldown_notifies_again(
        self, owner, laundry, audience, django_capture_on_commit_callbacks,
    ):
        self.put_promo(owner, {'enabled': True}, django_capture_on_commit_callbacks)
        self.put_promo(owner, {'enabled': False}, django_capture_on_commit_callbacks)
        Laundry.objects.filter(pk=laundry.pk).update(
            promo_last_notified_at=timezone.now() - promo_notifications.ANNOUNCE_COOLDOWN - timedelta(hours=1),
        )
        self.put_promo(owner, {'enabled': True}, django_capture_on_commit_callbacks)
        assert self.promo_notes().count() == 4

    def test_owner_cannot_make_simame_pay_or_change_rates(
        self, owner, laundry, pricing, django_capture_on_commit_callbacks,
    ):
        response = self.put_promo(owner, {'enabled': True, 'funded_by': 'SIMAME', 'pickup_price_per_km': '0'},
                                  django_capture_on_commit_callbacks)
        assert response.status_code == 200
        laundry.refresh_from_db()
        pricing.refresh_from_db()
        assert laundry.promo_funding_source == 'LAUNDRY'
        assert pricing.pickup_price_per_km == Decimal('3.00')

    def test_promo_needs_a_future_end_date(self, owner, laundry, django_capture_on_commit_callbacks):
        response = self.put_promo(owner, {'enabled': True, 'end_at': (timezone.now() - timedelta(days=1)).isoformat()},
                                  django_capture_on_commit_callbacks)
        assert response.status_code == 400

    def test_customers_cannot_use_the_owner_endpoint(self, customer):
        response = client_for(customer).put('/api/v1/laundries/dashboard/my-laundry/promotion/', {'enabled': True}, format='json')
        assert response.status_code == 403

    def test_push_is_queued_for_opted_in_devices(self, owner, laundry, audience, settings, django_capture_on_commit_callbacks):
        settings.EXPO_PUSH_ENABLED = True
        with patch('marketplace.services.notification_service.NotificationService._queue_push') as queue:
            self.put_promo(owner, {'enabled': True}, django_capture_on_commit_callbacks)
        assert queue.call_count == 2
