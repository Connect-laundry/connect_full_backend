"""
The owner half of Pay After: pricing a quote request into a payable invoice.

Without this the customer's pickup request would dead-end with no price and no
way to pay. These tests pin the loop: request created unpriced, laundry quotes
it, order becomes payable.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ordering.models import Order
from users.models import User

from test_booking_pricing_modes import _base_payload
from test_booking_create import _auth_client, _build_booking_catalog


def _quote_order(prefix):
    customer, laundry, *_ = _build_booking_catalog(prefix=prefix)
    client = _auth_client(customer)
    payload = _base_payload(laundry)
    payload.update({'pricing_mode': 'CUSTOM_QUOTE', 'special_instructions': '2 bags'})
    resp = client.post(reverse('booking-create'), payload, format='json')
    assert resp.status_code == status.HTTP_201_CREATED, resp.data
    order = Order.objects.get(id=resp.data['id'])
    return customer, laundry, order


@pytest.mark.django_db
class TestOwnerQuote:
    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_the_owner_can_price_a_quote_into_a_payable_invoice(self):
        # Matches the live config (free to use, no VAT), so the invoice total
        # equals the sum of the priced lines.
        _, laundry, order = _quote_order('Qi1')
        owner_client = _auth_client(laundry.owner)

        resp = owner_client.post(
            reverse('order-lifecycle-quote', args=[str(order.id)]),
            {'items': [
                {'name': 'Wash & fold 6kg', 'quantity': 1, 'price': '30.00'},
                {'name': 'Jacket dry clean', 'quantity': 1, 'price': '15.00'},
            ]},
            format='json',
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        order.refresh_from_db()
        assert order.items.count() == 2
        assert order.total_amount == Decimal('45.00')
        # Now frozen, so the customer sees this exact figure.
        assert order.priced_at is not None

    def test_a_customer_cannot_quote_their_own_order(self):
        customer, _, order = _quote_order('Qi2')
        client = _auth_client(customer)

        resp = client.post(
            reverse('order-lifecycle-quote', args=[str(order.id)]),
            {'items': [{'name': 'x', 'quantity': 1, 'price': '10.00'}]},
            format='json',
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_a_foreign_owner_cannot_quote(self):
        _, _, order = _quote_order('Qi3')
        intruder = User.objects.create_user(
            email='intruder@example.com', phone='233555933001',
            password='StrongPass123!', role=User.Role.OWNER,
        )
        resp = _auth_client(intruder).post(
            reverse('order-lifecycle-quote', args=[str(order.id)]),
            {'items': [{'name': 'x', 'quantity': 1, 'price': '10.00'}]},
            format='json',
        )
        assert resp.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)

    def test_quoting_twice_is_refused(self):
        _, laundry, order = _quote_order('Qi4')
        owner_client = _auth_client(laundry.owner)
        url = reverse('order-lifecycle-quote', args=[str(order.id)])
        items = {'items': [{'name': 'x', 'quantity': 1, 'price': '10.00'}]}

        assert owner_client.post(url, items, format='json').status_code == status.HTTP_200_OK
        # Second attempt must not double-invoice.
        assert owner_client.post(url, items, format='json').status_code == status.HTTP_400_BAD_REQUEST

    def test_an_itemised_order_cannot_be_quoted(self):
        # Only quote requests take this path; a normal order is already priced.
        from test_booking_create import _booking_payload

        customer, laundry, item, service_type, _ = _build_booking_catalog(prefix='Qi5')
        created = _auth_client(customer).post(
            reverse('booking-create'),
            _booking_payload(laundry, item, service_type),
            format='json',
        )
        order_id = created.data['id']

        resp = _auth_client(laundry.owner).post(
            reverse('order-lifecycle-quote', args=[order_id]),
            {'items': [{'name': 'x', 'quantity': 1, 'price': '10.00'}]},
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_empty_or_malformed_items_are_rejected(self):
        _, laundry, order = _quote_order('Qi6')
        owner_client = _auth_client(laundry.owner)
        url = reverse('order-lifecycle-quote', args=[str(order.id)])

        assert owner_client.post(url, {'items': []}, format='json').status_code == status.HTTP_400_BAD_REQUEST
        assert owner_client.post(
            url, {'items': [{'name': 'x', 'quantity': 1, 'price': 'free'}]}, format='json'
        ).status_code == status.HTTP_400_BAD_REQUEST
