"""
By-weight and pay-after-quote booking, end to end through the create endpoint.

Only itemised booking existed before; these two modes reached the checkout
screen but had no backend path, so the customer hit a dead Confirm button.
These tests pin the two new paths so they cannot silently regress.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.pricing import LaundryWeightPricing
from ordering.models import Order
from payments.models import Payment

from test_booking_create import _auth_client, _build_booking_catalog


def _base_payload(laundry):
    return {
        'laundry': str(laundry.id),
        'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
        'pickup_address': 'Pickup Address',
        'pickup_lat': '5.6037000',
        'pickup_lng': '-0.1870000',
        'delivery_address': 'Delivery Address',
        'delivery_lat': '5.6037000',
        'delivery_lng': '-0.1870000',
    }


def _add_weight_tariff(laundry, per_kg='2.00', minimum_charge='19.99',
                       minimum_weight=None, rounding='NONE'):
    return LaundryWeightPricing.objects.create(
        laundry=laundry,
        base_price_per_kg=Decimal(per_kg),
        minimum_charge=Decimal(minimum_charge),
        minimum_order_weight_kg=Decimal(minimum_weight) if minimum_weight else None,
        rounding_strategy=rounding,
        is_active=True,
    )


@pytest.mark.django_db
class TestByWeightBooking:
    @pytest.fixture(autouse=True)
    def _free(self, settings):
        # Isolate the weight price from tax/commission for clear assertions.
        settings.PLATFORM_FEE_RATE = 0.00
        settings.TAX_RATE = 0.00

    def test_by_weight_order_is_priced_from_the_tariff(self):
        customer, laundry, *_ = _build_booking_catalog(prefix='Wt1')
        _add_weight_tariff(laundry, per_kg='2.00', minimum_charge='0.00')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({'pricing_mode': 'BY_WEIGHT', 'estimated_weight_kg': '5.0'})

        resp = client.post(reverse('booking-create'), payload, format='json')

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        order = Order.objects.get(id=resp.data['id'])
        assert order.pricing_mode == Order.PricingMode.BY_WEIGHT
        assert order.estimated_weight_kg == Decimal('5.00')
        # 5kg * GHS 2.00 = GHS 10.00, priced server-side.
        assert order.total_amount == Decimal('10.00')
        # A single stand-in line item carries the price for receipts.
        assert order.items.count() == 1

    def test_the_minimum_charge_is_enforced_server_side(self):
        # The screenshot's laundry: GHS 2/kg with a GHS 19.99 floor. A light
        # 5kg load is GHS 10 on rate but must bill at the GHS 19.99 minimum.
        customer, laundry, *_ = _build_booking_catalog(prefix='Wt2')
        _add_weight_tariff(laundry, per_kg='2.00', minimum_charge='19.99')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({'pricing_mode': 'BY_WEIGHT', 'estimated_weight_kg': '5.0'})

        resp = client.post(reverse('booking-create'), payload, format='json')

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert Order.objects.get(id=resp.data['id']).total_amount == Decimal('19.99')

    def test_a_client_supplied_price_is_ignored(self):
        # Never trust the client's figure: only the tariff decides.
        customer, laundry, *_ = _build_booking_catalog(prefix='Wt3')
        _add_weight_tariff(laundry, per_kg='2.00', minimum_charge='0.00')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({
            'pricing_mode': 'BY_WEIGHT',
            'estimated_weight_kg': '5.0',
            'total_amount': '1.00',
        })

        resp = client.post(reverse('booking-create'), payload, format='json')
        # total_amount is not an accepted field, so it is rejected outright
        # rather than trusted.
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_by_weight_requires_a_weight(self):
        customer, laundry, *_ = _build_booking_catalog(prefix='Wt4')
        _add_weight_tariff(laundry)
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({'pricing_mode': 'BY_WEIGHT'})

        resp = client.post(reverse('booking-create'), payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'estimated_weight_kg' in resp.data

    def test_by_weight_needs_the_laundry_to_offer_it(self):
        # No tariff configured: the order is refused rather than priced at zero.
        customer, laundry, *_ = _build_booking_catalog(prefix='Wt5')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({'pricing_mode': 'BY_WEIGHT', 'estimated_weight_kg': '5.0'})

        resp = client.post(reverse('booking-create'), payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_by_weight_cod_stays_due_without_a_fake_payment(self):
        # COD is an explicit promise to pay at fulfillment, not a gateway payment.
        customer, laundry, *_ = _build_booking_catalog(prefix='Wt6')
        _add_weight_tariff(laundry, per_kg='2.00', minimum_charge='0.00')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({
            'pricing_mode': 'BY_WEIGHT',
            'estimated_weight_kg': '5.0',
            'payment_method': 'CASH',
        })

        resp = client.post(reverse('booking-create'), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['payment_method'] == 'CASH'
        assert resp.data['payment_state'] == 'CASH_DUE'
        assert resp.data['payment_reference'] is None
        assert resp.data['payment_intent']['authorization_url'] is None
        assert not Payment.objects.filter(order_id=resp.data['id']).exists()


@pytest.mark.django_db
class TestPayAfterQuoteBooking:
    def test_a_quote_request_creates_an_unpriced_order(self):
        customer, laundry, *_ = _build_booking_catalog(prefix='Q1')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({
            'pricing_mode': 'CUSTOM_QUOTE',
            'special_instructions': '2 bags of mixed laundry, 1 jacket',
        })

        resp = client.post(reverse('booking-create'), payload, format='json')

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        order = Order.objects.get(id=resp.data['id'])
        assert order.pricing_mode == Order.PricingMode.CUSTOM_QUOTE
        assert order.total_amount == Decimal('0.00')
        assert order.items.count() == 0
        # Not frozen: the laundry's later quote is what the customer will see,
        # not a zero snapshot.
        assert order.priced_at is None

    def test_a_quote_request_takes_no_payment(self):
        customer, laundry, *_ = _build_booking_catalog(prefix='Q2')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({'pricing_mode': 'CUSTOM_QUOTE'})

        resp = client.post(reverse('booking-create'), payload, format='json')

        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['payment_intent']['status'] == 'QUOTE_PENDING'
        assert not Payment.objects.filter(order_id=resp.data['id']).exists()

    def test_a_quote_request_needs_no_items_or_payment_method(self):
        customer, laundry, *_ = _build_booking_catalog(prefix='Q3')
        client = _auth_client(customer)

        # Deliberately no items, no payment_method.
        payload = _base_payload(laundry)
        payload.update({'pricing_mode': 'CUSTOM_QUOTE'})

        resp = client.post(reverse('booking-create'), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
class TestByItemUnchanged:
    def test_itemised_booking_still_rejects_empty_items(self):
        # The safety this whole change rests on: relaxing the field-level
        # allow_empty must not let an itemised order through with no items.
        customer, laundry, *_ = _build_booking_catalog(prefix='It1')
        client = _auth_client(customer)

        payload = _base_payload(laundry)
        payload.update({'pricing_mode': 'BY_ITEM', 'items': [], 'payment_method': 'CARD'})

        resp = client.post(reverse('booking-create'), payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'items' in resp.data

    def test_a_booking_with_no_mode_defaults_to_by_item(self):
        # Older clients send no pricing_mode. They must keep working exactly.
        from test_booking_create import _booking_payload

        customer, laundry, item, service_type, _ = _build_booking_catalog(prefix='It2')
        client = _auth_client(customer)

        resp = client.post(
            reverse('booking-create'),
            _booking_payload(laundry, item, service_type),
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert Order.objects.get(id=resp.data['id']).pricing_mode == Order.PricingMode.BY_ITEM
