import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.pricing import LaundryPricingItem
from laundries.models.service import LaundryService
from ordering.models import LaunderableItem, Order
from payments.models import Payment
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


def _build_pricing_catalog_laundry(prefix='Pricing'):
    """A laundry on the owner-defined LaundryPricingItem catalog.

    These laundries have no LaundryService/Category rows, so the vendor menu
    exposes the free-form ``category`` label as the service type.
    """
    owner = User.objects.create_user(
        email=f'{prefix.lower()}-owner@example.com',
        phone='233555902001',
        password='StrongPass123!',
        role=User.Role.OWNER,
    )
    customer = User.objects.create_user(
        email=f'{prefix.lower()}-customer@example.com',
        phone='233555902002',
        password='StrongPass123!',
    )
    laundry = Laundry.objects.create(
        name=f'{prefix} Laundry',
        description='Owner-priced test laundry',
        address='Accra',
        city='Accra',
        latitude='5.603700',
        longitude='-0.187000',
        phone_number='0240000003',
        owner=owner,
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
    )
    pricing_item = LaundryPricingItem.objects.create(
        laundry=laundry,
        item_name='Shirt (Wash Only)',
        category='Wash Only',
        unit_price='6.00',
        is_active=True,
    )
    return customer, laundry, pricing_item


@pytest.mark.django_db
class TestBookingCreatePricingCatalog:
    """Bookings against owner-defined price lists (no global Category rows).

    The client sends the free-form service label ("Wash Only") as
    ``service_type`` because that is all the vendor menu has. Feeding that into
    a UUID column used to raise ``"Wash Only" is not a valid UUID`` and fail
    every booking for these laundries.
    """

    @patch('ordering.views.order_views.PaymentService.create_payment_intent')
    def test_booking_create_accepts_non_uuid_service_label(self, mock_payment):
        customer, laundry, pricing_item = _build_pricing_catalog_laundry('LabelService')
        mock_payment.return_value = {
            'transaction_id': 'ORD-pricing-123',
            'amount': '12.00',
            'currency': 'GHS',
            'status': 'PENDING',
            'payment_method': 'CARD',
            'authorization_url': 'https://paystack.test/authorize',
            'access_code': 'access-pricing',
        }
        client = _auth_client(customer)
        payload = {
            'laundry': str(laundry.id),
            'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
            'pickup_address': 'Pickup Address',
            'delivery_address': 'Delivery Address',
            'items': [
                {
                    'item': str(pricing_item.id),
                    'service_type': 'Wash Only',
                    'quantity': 2,
                }
            ],
            'payment_method': 'CARD',
        }

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED, response.data
        order = Order.objects.get(id=response.data['id'])
        line = order.items.get()
        assert line.name == 'Shirt (Wash Only)'
        assert line.quantity == 2
        assert line.price == Decimal('6.00')
        # No global catalog rows exist for this laundry, so both FKs stay null.
        assert line.item is None
        assert line.service_type is None

    @patch('ordering.views.order_views.PaymentService.create_payment_intent')
    def test_booking_create_over_raw_api_path_returns_priced_order(self, mock_payment):
        """Full request path: literal URL, JSON body, middleware, serializer, DB."""
        customer, laundry, pricing_item = _build_pricing_catalog_laundry('RawPath')
        mock_payment.return_value = {
            'transaction_id': 'ORD-rawpath-1',
            'amount': '18.00',
            'currency': 'GHS',
            'status': 'PENDING',
            'payment_method': 'CARD',
            'authorization_url': 'https://paystack.test/authorize',
            'access_code': 'access-rawpath',
        }
        client = _auth_client(customer)

        response = client.post(
            '/api/v1/booking/create/',
            data={
                'laundry': str(laundry.id),
                'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
                'pickup_address': 'Accra Pickup',
                'delivery_address': 'Accra Delivery',
                'items': [
                    {
                        'item': str(pricing_item.id),
                        'service_type': 'Wash Only',
                        'quantity': 3,
                    }
                ],
                'payment_method': 'CARD',
            },
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        order = Order.objects.get(id=response.data['id'])
        assert order.order_no
        line = order.items.get()
        assert (line.name, line.quantity, line.price) == (
            'Shirt (Wash Only)',
            3,
            Decimal('6.00'),
        )
        # 3 x 6.00 must flow through into the persisted total.
        assert order.total_amount >= Decimal('18.00')

    def test_booking_create_rejects_unknown_pricing_item(self):
        customer, laundry, _ = _build_pricing_catalog_laundry('UnknownItem')
        client = _auth_client(customer)
        payload = {
            'laundry': str(laundry.id),
            'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
            'pickup_address': 'Pickup Address',
            'delivery_address': 'Delivery Address',
            'items': [
                {
                    'item': str(uuid.uuid4()),
                    'service_type': 'Wash Only',
                    'quantity': 1,
                }
            ],
            'payment_method': 'CARD',
        }

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0

    def test_booking_create_rejects_non_uuid_item_reference(self):
        customer, laundry, _ = _build_pricing_catalog_laundry('NonUuidItem')
        client = _auth_client(customer)
        payload = {
            'laundry': str(laundry.id),
            'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
            'pickup_address': 'Pickup Address',
            'delivery_address': 'Delivery Address',
            'items': [
                {
                    'item': 'Shirt (Wash Only)',
                    'service_type': 'Wash Only',
                    'quantity': 1,
                }
            ],
            'payment_method': 'CARD',
        }

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0


@pytest.mark.django_db
class TestBookingCreate:
    @patch('ordering.views.order_views.PaymentService.create_payment_intent')
    def test_booking_create_successfully_creates_order(self, mock_payment):
        customer, laundry, item, service_type, _ = _build_booking_catalog('Success')
        mock_payment.return_value = {
            'transaction_id': 'ORD-success-123',
            'amount': '72.50',
            'currency': 'GHS',
            'status': 'PENDING',
            'payment_method': 'CARD',
            'authorization_url': 'https://paystack.test/authorize',
            'access_code': 'access-123',
        }
        client = _auth_client(customer)

        response = client.post(
            reverse('booking-create'),
            _booking_payload(laundry, item, service_type),
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['id']
        assert response.data['items'][0]['quantity'] == 2
        assert response.data['payment_intent']['authorization_url'] == 'https://paystack.test/authorize'
        assert Order.objects.filter(id=response.data['id'], user=customer).count() == 1

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

    def test_booking_create_rejects_invalid_address_before_order_creation(self):
        customer, laundry, item, service_type, _ = _build_booking_catalog('InvalidAddress')
        client = _auth_client(customer)
        payload = _booking_payload(laundry, item, service_type)
        payload['pickup_address'] = ''

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'pickup_address' in response.data
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0

    def test_booking_create_rejects_invalid_laundry(self):
        customer, laundry, item, service_type, _ = _build_booking_catalog('InvalidLaundry')
        laundry.is_active = False
        laundry.save(update_fields=['is_active'])
        client = _auth_client(customer)

        response = client.post(
            reverse('booking-create'),
            _booking_payload(laundry, item, service_type),
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'laundry' in response.data
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0

    def test_booking_create_rejects_invalid_item(self):
        customer, laundry, _, service_type, _ = _build_booking_catalog('InvalidItem')
        other_category = Category.objects.create(
            name='InvalidItem Other Category',
            type=Category.CategoryType.ITEM_CATEGORY,
        )
        other_item = LaunderableItem.objects.create(
            name='InvalidItem Other Shirt',
            item_category=other_category,
        )
        client = _auth_client(customer)
        payload = _booking_payload(laundry, other_item, service_type)

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'items' in response.data
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0

    def test_booking_create_rejects_empty_cart(self):
        customer, laundry, item, service_type, _ = _build_booking_catalog('EmptyCart')
        client = _auth_client(customer)
        payload = _booking_payload(laundry, item, service_type)
        payload['items'] = []

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'items' in response.data
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0

    def test_booking_create_rejects_unsupported_schedule_payload_shape(self):
        customer, laundry, _, _, _ = _build_booking_catalog('ScheduleShape')
        client = _auth_client(customer)
        payload = {
            'laundry': str(laundry.id),
            'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
            'pickup_address_id': 'saved-address-id',
            'is_recurring': True,
            'frequency': 'weekly',
            'special_instructions': 'Leave with reception.',
        }

        response = client.post(reverse('booking-create'), payload, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'non_field_errors' in response.data
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 0

    @patch('ordering.views.order_views.PaymentService.create_payment_intent')
    def test_booking_create_idempotency_returns_cached_success(self, mock_payment):
        customer, laundry, item, service_type, _ = _build_booking_catalog('Idempotent')
        mock_payment.return_value = {
            'transaction_id': 'ORD-idempotent-123',
            'amount': '72.50',
            'currency': 'GHS',
            'status': 'PENDING',
            'payment_method': 'CARD',
            'authorization_url': 'https://paystack.test/authorize',
            'access_code': 'access-123',
        }
        client = _auth_client(customer)
        payload = _booking_payload(laundry, item, service_type)

        first = client.post(
            reverse('booking-create'),
            payload,
            format='json',
            HTTP_X_IDEMPOTENCY_KEY='booking-idempotency-key',
        )
        second = client.post(
            reverse('booking-create'),
            payload,
            format='json',
            HTTP_X_IDEMPOTENCY_KEY='booking-idempotency-key',
        )

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_201_CREATED
        assert second.headers['X-Idempotency-Cache'] == 'HIT'
        assert Order.objects.filter(user=customer, laundry=laundry).count() == 1
        assert mock_payment.call_count == 1

    def test_booking_create_requires_authentication(self):
        _, laundry, item, service_type, _ = _build_booking_catalog('Unauthenticated')
        client = APIClient()

        response = client.post(
            reverse('booking-create'),
            _booking_payload(laundry, item, service_type),
            format='json',
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch('payments.services.paystack.PaystackService.initialize_transaction')
    def test_booking_create_payment_provider_failure_is_retryable(self, mock_initialize):
        customer, laundry, item, service_type, _ = _build_booking_catalog('ProviderFailure')
        mock_initialize.return_value = {'status': False, 'message': 'Provider unavailable'}
        client = _auth_client(customer)

        response = client.post(
            reverse('booking-create'),
            _booking_payload(laundry, item, service_type),
            format='json',
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['payment_intent']['status'] == 'FAILED'
        assert response.data['payment_intent']['authorization_url'] is None


@pytest.mark.django_db
class TestCouponUsageLimit:
    @patch('ordering.views.order_views.PaymentService.create_payment_intent')
    def test_exhausted_coupon_is_rejected_and_count_is_atomic(self, mock_payment):
        from ordering.models.coupons import Coupon

        mock_payment.return_value = {
            'transaction_id': 'ORD-coupon-1',
            'amount': '10.00',
            'currency': 'GHS',
            'status': 'PENDING',
            'payment_method': 'CARD',
            'authorization_url': 'https://paystack.test/authorize',
            'access_code': 'access-1',
        }
        customer, laundry, item, service_type, _ = _build_booking_catalog('Coupon')
        coupon = Coupon.objects.create(
            code='SAVE10',
            discount_type=Coupon.DiscountType.FIXED,
            discount_value='10.00',
            max_usage=1,
            user_limit=5,
        )
        client = _auth_client(customer)
        payload = _booking_payload(laundry, item, service_type)
        payload['coupon_code'] = 'SAVE10'

        first = client.post(reverse('booking-create'), payload, format='json')
        assert first.status_code == status.HTTP_201_CREATED
        coupon.refresh_from_db()
        assert coupon.current_usage == 1

        # Second redemption must be rejected now that max_usage is reached.
        second = client.post(reverse('booking-create'), payload, format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        coupon.refresh_from_db()
        assert coupon.current_usage == 1
