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
