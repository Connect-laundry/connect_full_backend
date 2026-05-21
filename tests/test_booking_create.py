from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, Order
from users.models import User


def _auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _build_booking_catalog(prefix='Booking'):
    owner = User.objects.create_user(
        email=f'{prefix.lower()}-owner@example.com',
        phone='233555901001',
        password='StrongPass123!',
        role=User.Role.OWNER,
    )
    customer = User.objects.create_user(
        email=f'{prefix.lower()}-customer@example.com',
        phone='233555901002',
        password='StrongPass123!',
    )
    service_type = Category.objects.create(
        name=f'{prefix} Wash',
        type=Category.CategoryType.SERVICE_TYPE,
    )
    other_service_type = Category.objects.create(
        name=f'{prefix} Iron',
        type=Category.CategoryType.SERVICE_TYPE,
    )
    item_category = Category.objects.create(
        name=f'{prefix} Shirts',
        type=Category.CategoryType.ITEM_CATEGORY,
    )
    item = LaunderableItem.objects.create(
        name=f'{prefix} Shirt',
        item_category=item_category,
    )
    laundry = Laundry.objects.create(
        name=f'{prefix} Laundry',
        description='Booking test laundry',
        address='Accra',
        city='Accra',
        latitude='5.603700',
        longitude='-0.187000',
        phone_number='0240000002',
        owner=owner,
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
    )
    LaundryService.objects.create(
        laundry=laundry,
        item=item,
        service_type=service_type,
        price='25.00',
        is_available=True,
    )
    return customer, laundry, item, service_type, other_service_type


def _booking_payload(laundry, item, service_type):
    return {
        'laundry': str(laundry.id),
        'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
        'pickup_address': 'Pickup Address',
        'pickup_lat': '5.6037000',
        'pickup_lng': '-0.1870000',
        'delivery_address': 'Delivery Address',
        'delivery_lat': '5.6037000',
        'delivery_lng': '-0.1870000',
        'items': [
            {
                'item': str(item.id),
                'service_type': str(service_type.id),
                'quantity': 2,
            }
        ],
        'special_instructions': 'Handle carefully.',
        'payment_method': 'CARD',
    }


@pytest.mark.django_db
class TestBookingCreate:
    @patch('ordering.views.order_views.PaymentService.create_payment_intent')
    def test_booking_create_returns_order_when_payment_initialization_raises(self, mock_payment):
        customer, laundry, item, service_type, _ = _build_booking_catalog('PaymentFail')
        mock_payment.side_effect = RuntimeError('Paystack temporarily unavailable')
        client = _auth_client(customer)

        response = client.post(
            reverse('booking-create'),
            _booking_payload(laundry, item, service_type),
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['id']
        assert response.data['payment_intent']['status'] == 'FAILED'
        assert response.data['payment_intent']['authorization_url'] is None
        assert Order.objects.filter(id=response.data['id'], user=customer).exists()

    def test_booking_create_rolls_back_order_when_service_is_not_offered(self):
        customer, laundry, item, _, other_service_type = _build_booking_catalog('InvalidService')
        client = _auth_client(customer)
        payload = _booking_payload(laundry, item, other_service_type)

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0
