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

    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_end_to_end_come_and_take_my_items_flow(self):
        """Full end-to-end verification of the 9-step Come and Take My Items flow."""
        from payments.models import Payment
        from unittest.mock import patch

        customer, laundry, order = _quote_order('E2E1')
        customer_client = _auth_client(customer)
        owner_client = _auth_client(laundry.owner)

        # 1. Customer requested pickup with no upfront payment
        assert order.pricing_mode == Order.PricingMode.CUSTOM_QUOTE
        assert order.status == Order.Status.PENDING
        assert order.total_amount == Decimal('0.00')
        assert order.payment_status == Order.PaymentStatus.UNPAID
        assert order.priced_at is None

        # 2. Owner accepts the booking in Seller Center
        accept_resp = owner_client.patch(
            reverse('order-lifecycle-accept', args=[str(order.id)]),
            {},
            format='json',
        )
        assert accept_resp.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.CONFIRMED

        # 3. Owner/driver picks up the items from the customer
        pickup_resp = owner_client.patch(
            reverse('order-lifecycle-mark-picked-up', args=[str(order.id)]),
            {},
            format='json',
        )
        assert pickup_resp.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.PICKED_UP

        # 4. Owner inspects, counts/weighs items and submits quote via Seller Center
        quote_resp = owner_client.post(
            reverse('order-lifecycle-quote', args=[str(order.id)]),
            {'items': [
                {'name': '5 Shirts (Wash & Iron)', 'quantity': 1, 'price': '30.00'},
                {'name': '2 Jeans', 'quantity': 2, 'price': '15.00'},
            ]},
            format='json',
        )
        assert quote_resp.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.priced_at is not None
        assert order.total_amount == Decimal('60.00')

        # 5. Customer reviews quote in mobile app and initializes Paystack payment
        with patch('payments.services.paystack.PaystackService.initialize_transaction') as mock_paystack_init:
            mock_paystack_init.return_value = {
                'status': True,
                'data': {
                    'authorization_url': 'https://checkout.paystack.com/test_url',
                    'access_code': 'test_access',
                    'reference': 'test_ref_e2e',
                },
            }
            pay_init_resp = customer_client.post(
                reverse('payment_initialize'),
                {'order_id': str(order.id), 'payment_method': 'CARD'},
                format='json',
            )
            assert pay_init_resp.status_code == status.HTTP_200_OK, pay_init_resp.data
            ref = pay_init_resp.data['data']['reference']
            assert pay_init_resp.data['data']['authorization_url'] == 'https://checkout.paystack.com/test_url'

        # 6. Paystack verification succeeds
        with patch('payments.services.paystack.PaystackService.verify_transaction') as mock_paystack_verify:
            mock_paystack_verify.return_value = {
                'status': True,
                'data': {
                    'status': 'success',
                    'amount': 6000,
                    'currency': 'GHS',
                    'reference': ref,
                    'channel': 'card',
                    'metadata': {
                        'order_id': str(order.id),
                        'user_id': str(customer.id),
                    },
                },
            }
            verify_resp = customer_client.get(
                reverse('payment_verify', args=[ref]),
            )
            assert verify_resp.status_code == status.HTTP_200_OK, verify_resp.data

        order.refresh_from_db()
        assert order.payment_status == Order.PaymentStatus.PAID
        assert order.status == Order.Status.PICKED_UP

        # 7. Owner proceeds with washing
        wash_resp = owner_client.patch(
            reverse('order-lifecycle-mark-washed', args=[str(order.id)]),
            {},
            format='json',
        )
        assert wash_resp.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.IN_PROCESS

        # 8. Owner marks out for delivery
        out_resp = owner_client.patch(
            reverse('order-lifecycle-mark-out-for-delivery', args=[str(order.id)]),
            {},
            format='json',
        )
        assert out_resp.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.OUT_FOR_DELIVERY

        # 9. Delivery completed with customer handover code
        from ordering.services.handover import ensure_handover_code
        code = ensure_handover_code(order)
        deliver_resp = owner_client.patch(
            reverse('order-lifecycle-mark-delivered', args=[str(order.id)]),
            {'handover_code': code},
            format='json',
        )
        assert deliver_resp.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED

        # 10. Complete order
        complete_resp = owner_client.patch(
            reverse('order-lifecycle-complete', args=[str(order.id)]),
            {},
            format='json',
        )
        assert complete_resp.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.COMPLETED

    @override_settings(PLATFORM_FEE_RATE=0.00, TAX_RATE=0.00)
    def test_customer_declines_quote_after_pickup_cancels_cleanly(self):
        """Customer rejects quote after items were collected: order cancels without charge."""
        customer, laundry, order = _quote_order('E2E2')
        customer_client = _auth_client(customer)
        owner_client = _auth_client(laundry.owner)

        # Accept and pick up
        owner_client.patch(reverse('order-lifecycle-accept', args=[str(order.id)]), {}, format='json')
        owner_client.patch(reverse('order-lifecycle-mark-picked-up', args=[str(order.id)]), {}, format='json')
        order.refresh_from_db()
        assert order.status == Order.Status.PICKED_UP

        # Owner quotes
        owner_client.post(
            reverse('order-lifecycle-quote', args=[str(order.id)]),
            {'items': [{'name': 'Heavy items', 'quantity': 1, 'price': '120.00'}]},
            format='json',
        )
        order.refresh_from_db()
        assert order.total_amount == Decimal('120.00')

        # Customer declines quote and cancels order
        cancel_resp = customer_client.patch(
            reverse('order-lifecycle-cancel', args=[str(order.id)]),
            {'reason': 'declined_quote'},
            format='json',
        )
        assert cancel_resp.status_code == status.HTTP_200_OK, cancel_resp.data
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED
        assert order.payment_status == Order.PaymentStatus.UNPAID

