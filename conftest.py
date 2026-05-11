import os

import pytest
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.test_settings')
from django.core.cache import cache
from rest_framework.test import APIClient

from users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def authenticated_user(db):
    return User.objects.create_user(
        email='auth-user@example.com',
        phone='233000000000',
        password='StrongPass123!',
    )


@pytest.fixture
def auth_client(authenticated_user):
    client = APIClient()
    client.force_authenticate(user=authenticated_user)
    return client


@pytest.fixture(autouse=True)
def clear_test_cache():
    cache.clear()
    yield
    cache.clear()

from laundries.models.laundry import Laundry
from ordering.models import Order
from django.utils import timezone

@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email='admin@example.com',
        phone='233555900020',
        password='pass',
        role='ADMIN'
    )

@pytest.fixture
def other_owner(db):
    return User.objects.create_user(
        email='other-owner@example.com',
        phone='233555900010',
        password='pass',
        role='OWNER'
    )

@pytest.fixture
def sample_laundry(db, authenticated_user):
    owner = User.objects.create_user(
        email='laundry-owner@example.com', 
        phone='233555900011', 
        password='pass', 
        role='OWNER'
    )
    return Laundry.objects.create(name="Sample Laundry", owner=owner, address="Addr", latitude=5.6, longitude=-0.1, phone_number="0123456789")

@pytest.fixture
def sample_order(db, sample_laundry, authenticated_user):
    return Order.objects.create(
        user=authenticated_user,
        laundry=sample_laundry,
        status='PENDING',
        total_amount=50.00,
        pickup_date=timezone.now()
    )
