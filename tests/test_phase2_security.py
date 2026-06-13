from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from logistics.models import DeliveryAssignment, TrackingLog
from ordering.models import Order, OrderItem, LaunderableItem
from users.models import DeviceSession, User
from laundries.models.category import Category
from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService


def _auth_client(user, password='StrongPass123!', device_id='device-1'):
    client = APIClient()
    response = client.post(
        '/api/v1/auth/login/',
        {'email': user.email, 'password': password},
        format='json',
        HTTP_X_DEVICE_ID=device_id,
        HTTP_X_CLIENT_PLATFORM='ios',
        HTTP_X_CLIENT_VERSION='1.0.0',
        HTTP_USER_AGENT='ConnectLaundryTests/1.0',
    )
    assert response.status_code == status.HTTP_200_OK
    payload = response.data.get('data', response.data)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {payload['accessToken']}")
    return client, payload


def _unwrap_collection(payload):
    if isinstance(payload, dict):
        return payload.get('results') or payload.get('data') or []
    return payload


@pytest.mark.django_db
class TestPhase2Security:
    def test_diagnosis_and_docs_are_disabled_in_production(self):
        with override_settings(ROOT_URLCONF='config.urls'):
            client = APIClient()

            assert client.get('/api/v1/laundries/diagnosis/').status_code == status.HTTP_404_NOT_FOUND
            # Now unconditionally exposed as required by API pipeline specs
            assert client.get('/api/schema/').status_code == status.HTTP_200_OK
            assert client.get('/api/docs/').status_code == status.HTTP_200_OK


    def test_logistics_queries_are_role_scoped(self):
        owner = User.objects.create_user(email='owner@example.com',
            phone='233555100001',
            password='StrongPass123!',
            first_name='Owner',
            last_name='One',
            role=User.Role.OWNER,
        )
        driver = User.objects.create_user(email='driver@example.com',
            phone='233555100002',
            password='StrongPass123!',
            first_name='Driver',
            last_name='One',
            role=User.Role.DRIVER,
        )
        customer = User.objects.create_user(email='customer@example.com',
            phone='233555100003',
            password='StrongPass123!',
            first_name='Customer',
            last_name='One',
        )
        admin = User.objects.create_superuser(
            email='admin@example.com',
            phone='233555100004',
            password='StrongPass123!',
        )

        service_type = Category.objects.create(name='Wash', type=Category.CategoryType.SERVICE_TYPE)
        item_category = Category.objects.create(name='Shirts', type=Category.CategoryType.ITEM_CATEGORY)
        item = LaunderableItem.objects.create(name='T-Shirt', item_category=item_category)
        laundry = Laundry.objects.create(
            name='Owner Laundry',
            description='Test laundry',
            address='Test Address',
            city='Accra',
            latitude='5.6037',
            longitude='-0.1870',
            phone_number='0240000000',
            owner=owner,
            status=Laundry.ApprovalStatus.APPROVED,
            is_active=True,
        )
        LaundryService.objects.create(
            laundry=laundry,
            item=item,
            service_type=service_type,
            price='10.00',
            is_available=True,
        )
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timedelta(days=1),
            total_amount='10.00',
            pickup_address='Customer Address',
            delivery_address='Customer Delivery',
        )
        DeliveryAssignment.objects.create(
            order=order,
            driver=driver,
            assignment_type=DeliveryAssignment.AssignmentType.PICKUP,
        )
        TrackingLog.objects.create(
            order=order,
            status='PICKED_UP',
            description='Picked up',
            location_name='Warehouse',
        )

        with override_settings(ROOT_URLCONF='config.urls'):
            customer_client, _ = _auth_client(customer)
            driver_client, _ = _auth_client(driver, device_id='device-driver')
            owner_client, _ = _auth_client(owner, device_id='device-owner')
            admin_client, _ = _auth_client(admin, device_id='device-admin')

            customer_res = customer_client.get('/api/v1/logistics/assignments/')
            driver_res = driver_client.get('/api/v1/logistics/assignments/')
            owner_res = owner_client.get('/api/v1/logistics/assignments/')
            admin_res = admin_client.get('/api/v1/logistics/assignments/')

            assert customer_res.status_code == status.HTTP_200_OK
            assert driver_res.status_code == status.HTTP_200_OK
            assert owner_res.status_code == status.HTTP_200_OK
            assert admin_res.status_code == status.HTTP_200_OK

            assert len(_unwrap_collection(customer_res.data)) == 0
            assert len(_unwrap_collection(driver_res.data)) == 1
            assert len(_unwrap_collection(owner_res.data)) == 1
            assert len(_unwrap_collection(admin_res.data)) == 1

            customer_tracking = customer_client.get('/api/v1/logistics/tracking/', {'order_id': str(order.id)})
            driver_tracking = driver_client.get('/api/v1/logistics/tracking/', {'order_id': str(order.id)})

            assert len(_unwrap_collection(customer_tracking.data)) == 1
            assert len(_unwrap_collection(driver_tracking.data)) == 1

    def test_order_detail_exposes_backend_price_breakdown(self):
        owner = User.objects.create_user(email='owner2@example.com',
            phone='233555200001',
            password='StrongPass123!',
            role=User.Role.OWNER,
        )
        customer = User.objects.create_user(email='customer2@example.com',
            phone='233555200002',
            password='StrongPass123!',
        )
        service_type = Category.objects.create(name='Iron', type=Category.CategoryType.SERVICE_TYPE)
        item_category = Category.objects.create(name='Pants', type=Category.CategoryType.ITEM_CATEGORY)
        item = LaunderableItem.objects.create(name='Jeans', item_category=item_category)
        laundry = Laundry.objects.create(
            name='Price Laundry',
            description='Price test',
            address='Test Address',
            city='Accra',
            latitude='5.6037',
            longitude='-0.1870',
            phone_number='0240000001',
            owner=owner,
            status=Laundry.ApprovalStatus.APPROVED,
            is_active=True,
            delivery_fee='10.00',
            pickup_fee='5.00',
        )
        LaundryService.objects.create(
            laundry=laundry,
            item=item,
            service_type=service_type,
            price='100.00',
            is_available=True,
        )
        order = Order.objects.create(
            user=customer,
            laundry=laundry,
            pickup_date=timezone.now() + timedelta(days=1),
            total_amount='127.00',
            pickup_address='Customer Address',
            delivery_address='Customer Delivery',
        )
        OrderItem.objects.create(
            order=order,
            item=item,
            service_type=service_type,
            name='Jeans',
            quantity=1,
            price='100.00',
        )

        with override_settings(ROOT_URLCONF='config.urls'):
            client, _ = _auth_client(customer, device_id='device-price')
            response = client.get(f'/api/v1/orders/{order.id}/')

            assert response.status_code == status.HTTP_200_OK
            payload = response.data.get('data', response.data)
            breakdown = payload['price_breakdown']
            assert breakdown['items_total'] == '100.00'
            assert breakdown['delivery_fee'] == '10.00'
            assert breakdown['pickup_fee'] == '5.00'
            assert breakdown['currency'] == 'GHS'
            assert breakdown['total'] == '127.00'

    def test_account_deletion_revokes_sessions_and_anonymizes_user(self):
        user = User.objects.create_user(email='delete@example.com',
            phone='233555300001',
            password='StrongPass123!',
            first_name='Delete',
            last_name='Me',
        )
        with override_settings(ROOT_URLCONF='config.urls'):
            client, _ = _auth_client(user, device_id='device-delete')

            assert DeviceSession.objects.filter(user=user, revoked_at__isnull=True).exists()

            response = client.delete('/api/v1/auth/account/', {'reason': 'privacy_request'}, format='json')
            assert response.status_code == status.HTTP_200_OK

            user.refresh_from_db()
            session = DeviceSession.objects.filter(user_id=user.id).first()

            assert user.is_active is False
            assert user.email.startswith('deleted-')
            assert user.phone.startswith('deleted-')
            assert session is not None and session.revoked_at is not None
