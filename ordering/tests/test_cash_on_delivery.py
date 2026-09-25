from datetime import timedelta
from decimal import Decimal
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.pricing import LaundryWeightPricing
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, Order, OrderItem
from payments.models import Payment, OrderSettlement
from users.models import User


def _auth_client(user: User):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _setup_cod_environment():
    suffix = uuid.uuid4().hex[:6]
    owner = User.objects.create_user(
        email=f'owner-cod-{suffix}@example.com',
        phone=f'23355{suffix[:7]}',
        password='StrongPass123!',
        role=User.Role.OWNER,
    )
    customer = User.objects.create_user(
        email=f'customer-cod-{suffix}@example.com',
        phone=f'23356{suffix[:7]}',
        password='StrongPass123!',
    )
    service_type = Category.objects.create(name=f'COD Wash {suffix}', type=Category.CategoryType.SERVICE_TYPE)
    item_category = Category.objects.create(name=f'COD Category {suffix}', type=Category.CategoryType.ITEM_CATEGORY)
    item = LaunderableItem.objects.create(name=f'COD Shirt {suffix}', item_category=item_category)

    laundry = Laundry.objects.create(
        name=f'COD Laundry {suffix}',
        description='Cash on Delivery test laundry',
        address='123 Oxford Street, Osu, Accra',
        city='Accra',
        latitude='5.6037',
        longitude='-0.1870',
        phone_number='0241112233',
        owner=owner,
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
    )

    laundry_service = LaundryService.objects.create(
        laundry=laundry,
        item=item,
        service_type=service_type,
        price=Decimal('35.00'),
        is_available=True,
    )

    weight_pricing = LaundryWeightPricing.objects.create(
        laundry=laundry,
        base_price_per_kg=Decimal('10.00'),
        minimum_charge=Decimal('20.00'),
        minimum_order_weight_kg=Decimal('2.00'),
        is_active=True,
    )

    return owner, customer, laundry, laundry_service, weight_pricing


@pytest.mark.django_db
class TestCashOnDeliveryEndToEnd:
    """Complete end-to-end test suite for Cash on Delivery flow in production."""

    def test_cod_itemised_booking_creation(self):
        owner, customer, laundry, service, _ = _setup_cod_environment()
        client = _auth_client(customer)

        response = client.post(
            reverse('booking-create'),
            {
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
                'pickup_address': 'Airport Residential Area, Accra',
                'delivery_address': 'Airport Residential Area, Accra',
                'payment_method': 'cash_on_delivery',
                'items': [{
                    'item': str(service.item_id),
                    'service_type': str(service.service_type_id),
                    'quantity': 2,
                }],
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.data
        order = Order.objects.get(pk=data['id'])

        # Verify order normalization and attributes
        assert order.payment_method == Order.PaymentMethod.CASH
        assert order.payment_status == Order.PaymentStatus.UNPAID
        assert order.total_amount > Decimal('0.00')

        # Verify API response contract
        assert data['payment_state'] == 'CASH_DUE'
        assert data['provider_payment_status'] is None
        assert data['payment_reference'] is None
        assert Decimal(data['amount_due']) == order.total_amount
        assert Decimal(data['amount_collected']) == Decimal('0.00')
        assert data['cash_collected_at'] is None

        # Verify payment intent block
        assert data['payment_intent']['status'] == 'CASH_DUE'
        assert data['payment_intent']['payment_method'] == 'CASH'
        assert data['payment_intent']['authorization_url'] is None
        assert data['payment_intent']['transaction_id'] is None
        assert Decimal(data['payment_intent']['amount']) == order.total_amount

        # Confirm no premature Payment record was created
        assert not Payment.objects.filter(order=order).exists()

    def test_cod_by_weight_booking_creation(self):
        owner, customer, laundry, _, weight_pricing = _setup_cod_environment()
        client = _auth_client(customer)

        response = client.post(
            reverse('booking-create'),
            {
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
                'pickup_address': 'East Legon, Accra',
                'delivery_address': 'East Legon, Accra',
                'payment_method': 'CASH',
                'pricing_mode': 'BY_WEIGHT',
                'estimated_weight_kg': '6.00',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.data
        order = Order.objects.get(pk=data['id'])

        # Base 20 for 2kg + 4kg extra * 5 = 40 + fees
        assert order.pricing_mode == Order.PricingMode.BY_WEIGHT
        assert order.payment_method == Order.PaymentMethod.CASH
        assert order.estimated_weight_kg == Decimal('6.00')
        assert order.total_amount > Decimal('40.00')
        assert data['payment_state'] == 'CASH_DUE'
        assert data['payment_intent']['status'] == 'CASH_DUE'
        assert data['payment_intent']['authorization_url'] is None

    def test_cod_full_lifecycle_progression_and_cash_collection(self):
        owner, customer, laundry, service, _ = _setup_cod_environment()
        cust_client = _auth_client(customer)
        owner_client = _auth_client(owner)

        # 1. Create order
        create_res = cust_client.post(
            reverse('booking-create'),
            {
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
                'pickup_address': 'Cantonments, Accra',
                'delivery_address': 'Cantonments, Accra',
                'payment_method': 'CASH',
                'items': [{
                    'item': str(service.item_id),
                    'service_type': str(service.service_type_id),
                    'quantity': 1,
                }],
            },
            format='json',
        )
        order_id = create_res.data['id']
        order = Order.objects.get(pk=order_id)
        total_str = str(order.total_amount)

        # 2. Accept order (CONFIRMED) — succeeds without upfront payment
        accept_res = owner_client.patch(
            reverse('order-lifecycle-accept', kwargs={'pk': order_id}),
            {},
            format='json',
        )
        assert accept_res.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.CONFIRMED

        # 3. Transitions through pickup and processing
        transitions = [
            'order-lifecycle-mark-picked-up',
            'order-lifecycle-mark-washed',
            'order-lifecycle-mark-out-for-delivery',
        ]
        for route in transitions:
            res = owner_client.patch(reverse(route, kwargs={'pk': order_id}), {}, format='json')
            assert res.status_code == status.HTTP_200_OK

        order.refresh_from_db()
        assert order.status == Order.Status.OUT_FOR_DELIVERY

        # 4. Attempt to complete BEFORE collecting cash — MUST be blocked (409 Conflict)
        blocked_complete = owner_client.patch(
            reverse('order-lifecycle-complete', kwargs={'pk': order_id}),
            {},
            format='json',
        )
        assert blocked_complete.status_code == status.HTTP_409_CONFLICT
        assert 'cash collection' in blocked_complete.data['message'].lower()

        # 5. Owner collects cash
        collect_res = owner_client.post(
            reverse('order-lifecycle-collect-cash', kwargs={'pk': order_id}),
            {'amount': total_str},
            format='json',
        )
        assert collect_res.status_code == status.HTTP_200_OK
        assert collect_res.data['already_collected'] is False
        assert collect_res.data['data']['payment_state'] == 'CASH_COLLECTED'
        assert Decimal(collect_res.data['data']['amount_due']) == Decimal('0.00')
        assert Decimal(collect_res.data['data']['amount_collected']) == order.total_amount

        # Verify Payment and Order state in database
        order.refresh_from_db()
        assert order.payment_status == Order.PaymentStatus.PAID
        payment = Payment.objects.get(order=order)
        assert payment.payment_method == Payment.Method.CASH
        assert payment.status == Payment.Status.SUCCESS
        assert payment.amount_collected == order.total_amount
        assert payment.collected_by == owner
        assert payment.paid_at is not None

        # 6. Deliver and Complete order — succeeds now that cash is collected!
        deliver_res = owner_client.patch(
            reverse('order-lifecycle-mark-delivered', kwargs={'pk': order_id}),
            {},
            format='json',
        )
        assert deliver_res.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.DELIVERED

        complete_res = owner_client.patch(
            reverse('order-lifecycle-complete', kwargs={'pk': order_id}),
            {},
            format='json',
        )
        assert complete_res.status_code == status.HTTP_200_OK
        order.refresh_from_db()
        assert order.status == Order.Status.COMPLETED

    def test_cod_cash_collection_guards(self):
        owner, customer, laundry, service, _ = _setup_cod_environment()
        cust_client = _auth_client(customer)
        owner_client = _auth_client(owner)

        other_owner = User.objects.create_user(
            email='stranger-owner@example.com',
            phone='233599998877',
            password='StrongPass123!',
            role=User.Role.OWNER,
        )
        stranger_client = _auth_client(other_owner)

        # Create order
        create_res = cust_client.post(
            reverse('booking-create'),
            {
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
                'pickup_address': 'Dzorwulu, Accra',
                'delivery_address': 'Dzorwulu, Accra',
                'payment_method': 'CASH',
                'items': [{
                    'item': str(service.item_id),
                    'service_type': str(service.service_type_id),
                    'quantity': 1,
                }],
            },
            format='json',
        )
        order_id = create_res.data['id']
        order = Order.objects.get(pk=order_id)
        total_str = str(order.total_amount)
        collect_url = reverse('order-lifecycle-collect-cash', kwargs={'pk': order_id})

        # Guard A: Customer cannot call collect_cash
        assert cust_client.post(collect_url, {'amount': total_str}).status_code == status.HTTP_403_FORBIDDEN

        # Guard B: Another laundry's owner cannot call collect_cash
        assert stranger_client.post(collect_url, {'amount': total_str}).status_code == status.HTTP_403_FORBIDDEN

        # Guard C: Calling too early (when status is PENDING) fails
        assert owner_client.post(collect_url, {'amount': total_str}).status_code == status.HTTP_409_CONFLICT

        # Move to OUT_FOR_DELIVERY
        owner_client.patch(reverse('order-lifecycle-accept', kwargs={'pk': order_id}), {})
        owner_client.patch(reverse('order-lifecycle-mark-picked-up', kwargs={'pk': order_id}), {})
        owner_client.patch(reverse('order-lifecycle-mark-washed', kwargs={'pk': order_id}), {})
        owner_client.patch(reverse('order-lifecycle-mark-out-for-delivery', kwargs={'pk': order_id}), {})

        # Guard D: Wrong amount fails (400 Bad Request)
        wrong_amt = str(order.total_amount - Decimal('5.00'))
        wrong_res = owner_client.post(collect_url, {'amount': wrong_amt})
        assert wrong_res.status_code == status.HTTP_400_BAD_REQUEST

        # Guard E: Correct collection succeeds
        success_res = owner_client.post(collect_url, {'amount': total_str})
        assert success_res.status_code == status.HTTP_200_OK

        # Guard F: Re-calling is idempotent (returns already_collected: True without duplicate payment)
        replay_res = owner_client.post(collect_url, {'amount': total_str})
        assert replay_res.status_code == status.HTTP_200_OK
        assert replay_res.data['already_collected'] is True
        assert Payment.objects.filter(order=order).count() == 1

    def test_cod_cancellation_by_customer_never_refunds_paystack(self):
        owner, customer, laundry, service, _ = _setup_cod_environment()
        cust_client = _auth_client(customer)

        create_res = cust_client.post(
            reverse('booking-create'),
            {
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
                'pickup_address': 'Labone, Accra',
                'delivery_address': 'Labone, Accra',
                'payment_method': 'CASH',
                'items': [{
                    'item': str(service.item_id),
                    'service_type': str(service.service_type_id),
                    'quantity': 1,
                }],
            },
            format='json',
        )
        order_id = create_res.data['id']

        with patch('payments.services.paystack.PaystackService.refund_transaction') as mock_refund:
            cancel_res = cust_client.patch(
                reverse('order-lifecycle-cancel', kwargs={'pk': order_id}),
                {'reason': 'Plans changed'},
                format='json',
            )
            assert cancel_res.status_code == status.HTTP_200_OK
            mock_refund.assert_not_called()

        order = Order.objects.get(pk=order_id)
        assert order.status == Order.Status.CANCELLED
        assert not Payment.objects.filter(order=order).exists()
        assert not OrderSettlement.objects.filter(order=order).exists()
