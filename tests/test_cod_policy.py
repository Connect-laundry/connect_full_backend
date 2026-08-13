from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from ordering.models import Order
from laundries.models.service import LaundryService
from payments.models import OrderSettlement, Payment
from test_payments import _auth_client, _build_order
from users.models import User


def _cash_order(*, status_value=Order.Status.PENDING, pricing_mode=Order.PricingMode.BY_ITEM):
    customer, order = _build_order()
    order.payment_method = Order.PaymentMethod.CASH
    order.pricing_mode = pricing_mode
    order.status = status_value
    order.save(update_fields=['payment_method', 'pricing_mode', 'status', 'updated_at'])
    return customer, order


@pytest.mark.django_db
class TestCodContract:
    def test_cod_booking_creates_no_fake_payment_reference(self):
        customer, source = _build_order()
        source.delete()
        laundry = source.laundry
        service = LaundryService.objects.select_related('item', 'service_type').get(laundry=laundry)

        response = _auth_client(customer).post(
            reverse('booking-create'),
            {
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
                'pickup_address': 'Pickup Address',
                'delivery_address': 'Delivery Address',
                'payment_method': 'CASH',
                'items': [{
                    'item': str(service.item_id),
                    'service_type': str(service.service_type_id),
                    'quantity': 1,
                }],
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        payload = response.data.get('data', response.data)
        order = Order.objects.get(pk=payload['id'])
        assert order.payment_method == Order.PaymentMethod.CASH
        assert payload['payment_state'] == 'CASH_DUE'
        assert payload['payment_reference'] is None
        assert payload['provider_payment_status'] is None
        assert payload['payment_intent']['status'] == 'CASH_DUE'
        assert payload['payment_intent']['transaction_id'] is None
        assert not Payment.objects.filter(order=order).exists()

    def test_cod_and_custom_quote_can_be_accepted_before_payment(self):
        _, cash_order = _cash_order()
        response = _auth_client(cash_order.laundry.owner).patch(
            reverse('order-lifecycle-accept', kwargs={'pk': cash_order.id}),
            {},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK

        cash_order.refresh_from_db()
        assert cash_order.status == Order.Status.CONFIRMED
        assert cash_order.payment_status == Order.PaymentStatus.UNPAID

    def test_custom_quote_online_can_be_accepted_before_payment(self):
        _, order = _build_order()
        order.pricing_mode = Order.PricingMode.CUSTOM_QUOTE
        order.payment_method = Order.PaymentMethod.CARD
        order.save(update_fields=['pricing_mode', 'payment_method', 'updated_at'])

        response = _auth_client(order.laundry.owner).patch(
            reverse('order-lifecycle-accept', kwargs={'pk': order.id}),
            {},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.CONFIRMED
        assert order.payment_status == Order.PaymentStatus.UNPAID

    def test_ordinary_unpaid_online_order_cannot_be_accepted_without_payment_row(self):
        _, order = _build_order()

        response = _auth_client(order.laundry.owner).patch(
            reverse('order-lifecycle-accept', kwargs={'pk': order.id}),
            {},
            format='json',
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        order.refresh_from_db()
        assert order.status == Order.Status.PENDING

    def test_accepted_priced_custom_quote_can_initialize_paystack(self):
        customer, order = _build_order()
        order.pricing_mode = Order.PricingMode.CUSTOM_QUOTE
        order.payment_method = Order.PaymentMethod.CARD
        order.status = Order.Status.CONFIRMED
        order.priced_at = timezone.now()
        order.save(update_fields=['pricing_mode', 'payment_method', 'status', 'priced_at', 'updated_at'])

        with patch(
            'payments.views.PaystackService.initialize_transaction',
            return_value={
                'status': True,
                'data': {
                    'access_code': 'accepted-quote-code',
                    'authorization_url': 'https://checkout.paystack.com/accepted-quote-code',
                },
            },
        ):
            response = _auth_client(customer).post(
                reverse('payment_initialize'),
                {'order_id': str(order.id), 'payment_method': 'CARD'},
                format='json',
            )

        assert response.status_code == status.HTTP_200_OK
        payment = Payment.objects.get(order=order)
        assert payment.status == Payment.Status.PENDING
        assert payment.transaction_reference
        assert payment.paystack_reference == 'accepted-quote-code'


    def test_cod_runs_full_fulfillment_but_requires_collection_before_completion(self):
        _, order = _cash_order()
        client = _auth_client(order.laundry.owner)
        steps = [
            ('order-lifecycle-accept', {}),
            ('order-lifecycle-mark-picked-up', {}),
            ('order-lifecycle-mark-washed', {}),
            ('order-lifecycle-mark-out-for-delivery', {}),
            ('order-lifecycle-mark-delivered', {}),
        ]
        for route_name, body in steps:
            response = client.patch(
                reverse(route_name, kwargs={'pk': order.id}),
                body,
                format='json',
            )
            assert response.status_code == status.HTTP_200_OK

        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED
        assert order.payment_status == Order.PaymentStatus.UNPAID
        assert client.patch(
            reverse('order-lifecycle-complete', kwargs={'pk': order.id}),
            {},
            format='json',
        ).status_code == status.HTTP_409_CONFLICT

        assert client.post(
            reverse('order-lifecycle-collect-cash', kwargs={'pk': order.id}),
            {'amount': '25.00'},
            format='json',
        ).status_code == status.HTTP_200_OK
        assert client.patch(
            reverse('order-lifecycle-complete', kwargs={'pk': order.id}),
            {},
            format='json',
        ).status_code == status.HTTP_200_OK

    def test_cod_cannot_enter_paystack_initialization(self):
        customer, order = _cash_order()

        response = _auth_client(customer).post(
            reverse('payment_initialize'),
            {'order_id': str(order.id), 'payment_method': 'CASH'},
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Payment.objects.filter(order=order).exists()

    @patch('payments.services.paystack.PaystackService.refund_transaction')
    def test_unpaid_cod_cancellation_never_calls_paystack_refund(self, refund):
        customer, order = _cash_order()

        response = _auth_client(customer).patch(
            reverse('order-lifecycle-cancel', kwargs={'pk': order.id}),
            {'reason': 'No longer needed'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        refund.assert_not_called()
        assert not Payment.objects.filter(order=order).exists()
        assert not OrderSettlement.objects.filter(order=order).exists()

@pytest.mark.django_db
class TestCashCollection:
    def test_collection_rejects_customer_wrong_state_wrong_amount_and_non_cash(self):
        customer, order = _cash_order(status_value=Order.Status.OUT_FOR_DELIVERY)
        url = reverse('order-lifecycle-collect-cash', kwargs={'pk': order.id})

        assert _auth_client(customer).post(
            url, {'amount': '25.00'}, format='json'
        ).status_code == status.HTTP_403_FORBIDDEN

        order.status = Order.Status.CONFIRMED
        order.save(update_fields=['status', 'updated_at'])
        assert _auth_client(order.laundry.owner).post(
            url, {'amount': '25.00'}, format='json'
        ).status_code == status.HTTP_409_CONFLICT

        order.status = Order.Status.OUT_FOR_DELIVERY
        order.save(update_fields=['status', 'updated_at'])
        assert _auth_client(order.laundry.owner).post(
            url, {'amount': '24.99'}, format='json'
        ).status_code == status.HTTP_400_BAD_REQUEST

        order.payment_method = Order.PaymentMethod.CARD
        order.save(update_fields=['payment_method', 'updated_at'])
        assert _auth_client(order.laundry.owner).post(
            url, {'amount': '25.00'}, format='json'
        ).status_code == status.HTTP_409_CONFLICT

    def test_other_owner_cannot_collect_cash_for_this_laundry(self):
        _, order = _cash_order(status_value=Order.Status.OUT_FOR_DELIVERY)
        other_owner = User.objects.create_user(
            email='other-owner@example.com',
            phone='233555901099',
            password='StrongPass123!',
            role=User.Role.OWNER,
        )

        response = _auth_client(other_owner).post(
            reverse('order-lifecycle-collect-cash', kwargs={'pk': order.id}),
            {'amount': '25.00'},
            format='json',
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not Payment.objects.filter(order=order).exists()

    def test_custom_quote_cod_uses_the_same_collection_rules(self):
        _, order = _cash_order(
            status_value=Order.Status.DELIVERED,
            pricing_mode=Order.PricingMode.CUSTOM_QUOTE,
        )

        response = _auth_client(order.laundry.owner).post(
            reverse('order-lifecycle-collect-cash', kwargs={'pk': order.id}),
            {'amount': '25.00'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.payment_status == Order.PaymentStatus.PAID
        assert Payment.objects.get(order=order).payment_method == Payment.Method.CASH
        assert not OrderSettlement.objects.filter(order=order).exists()
    def test_collection_is_audited_idempotent_and_never_creates_settlement(self):
        _, order = _cash_order(status_value=Order.Status.OUT_FOR_DELIVERY)
        owner = order.laundry.owner
        url = reverse('order-lifecycle-collect-cash', kwargs={'pk': order.id})

        response = _auth_client(owner).post(url, {'amount': '25.00'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['already_collected'] is False
        assert response.data['data']['payment_state'] == 'CASH_COLLECTED'
        assert response.data['data']['amount_due'] == '0.00'
        assert response.data['data']['amount_collected'] == '25.00'
        assert response.data['data']['payment_reference'] is None
        assert response.data['data']['provider_payment_status'] is None

        order.refresh_from_db()
        payment = Payment.objects.get(order=order)
        assert order.payment_status == Order.PaymentStatus.PAID
        assert payment.payment_method == Payment.Method.CASH
        assert payment.status == Payment.Status.SUCCESS
        assert payment.amount_collected == Decimal('25.00')
        assert payment.transaction_reference is None
        assert payment.paystack_reference is None
        assert payment.collected_by == owner
        assert payment.paid_at is not None
        assert not OrderSettlement.objects.filter(order=order).exists()

        replay = _auth_client(owner).post(url, {'amount': '25.00'}, format='json')
        assert replay.status_code == status.HTTP_200_OK
        assert replay.data['already_collected'] is True
        assert Payment.objects.filter(order=order).count() == 1
        assert not OrderSettlement.objects.filter(order=order).exists()

    def test_unpaid_custom_quote_is_not_owner_earnings(self):
        _, order = _build_order()
        order.status = Order.Status.DELIVERED
        order.pricing_mode = Order.PricingMode.CUSTOM_QUOTE
        order.payment_method = Order.PaymentMethod.CARD
        order.save(update_fields=['status', 'pricing_mode', 'payment_method', 'updated_at'])

        response = _auth_client(order.laundry.owner).get(reverse('dashboard-earnings'))

        assert response.status_code == status.HTTP_200_OK
        assert Decimal(response.data['data']['total_revenue']) == Decimal('0')
    def test_uncollected_cod_is_not_earnings_and_cash_is_not_a_payout_balance(self):
        _, order = _cash_order(status_value=Order.Status.DELIVERED)
        owner_client = _auth_client(order.laundry.owner)

        unpaid = owner_client.get(reverse('dashboard-earnings'))
        assert unpaid.status_code == status.HTTP_200_OK
        assert Decimal(unpaid.data['data']['total_revenue']) == Decimal('0')

        owner_client.post(
            reverse('order-lifecycle-collect-cash', kwargs={'pk': order.id}),
            {'amount': '25.00'},
            format='json',
        )
        paid = owner_client.get(reverse('dashboard-earnings'))
        assert Decimal(paid.data['data']['total_revenue']) == Decimal('25.00')

        payouts = owner_client.get(reverse('dashboard-payouts'))
        assert payouts.status_code == status.HTTP_200_OK
        assert Decimal(payouts.data['data']['summary']['cash_collected']) == Decimal('25.00')
        assert Decimal(payouts.data['data']['summary']['available']) == Decimal('0')
        assert Decimal(payouts.data['data']['summary']['held']) == Decimal('0')



@pytest.mark.django_db(transaction=True)
def test_concurrent_cash_collection_creates_one_payment():
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections, connection

    if connection.vendor == 'sqlite':
        pytest.skip('SQLite does not provide the row-level locking used in production.')
    from rest_framework.test import APIClient

    _, order = _cash_order(status_value=Order.Status.OUT_FOR_DELIVERY)
    owner_id = order.laundry.owner_id
    url = reverse('order-lifecycle-collect-cash', kwargs={'pk': order.id})

    def collect():
        close_old_connections()
        owner = User.objects.get(pk=owner_id)
        client = APIClient()
        client.force_authenticate(user=owner)
        response = client.post(url, {'amount': '25.00'}, format='json')
        close_old_connections()
        return response.status_code, response.data.get('already_collected')

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: collect(), range(2)))

    assert sorted(results) == [
        (status.HTTP_200_OK, False),
        (status.HTTP_200_OK, True),
    ]
    assert Payment.objects.filter(order=order).count() == 1
    assert not OrderSettlement.objects.filter(order=order).exists()
