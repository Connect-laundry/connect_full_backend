"""Regression tests for self-registration role handling.

Guards the fix for the bug where the frontend sent role="OWNER" but the
backend silently created the user as CUSTOMER (role was missing from the
RegisterSerializer). Owner web signup must be able to create OWNER users, while
privileged roles (ADMIN/DRIVER) must never be self-assignable from a public
request.
"""
import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from users.models import User


def _register(client, **overrides):
    payload = {
        'email': 'reg-user@example.com',
        'phone': '233200000000',
        'first_name': 'Reg',
        'last_name': 'User',
        'password': 'StrongPass123!',
        'password_confirm': 'StrongPass123!',
    }
    payload.update(overrides)
    return client.post(reverse('auth_register'), data=payload, format='json')


@pytest.mark.django_db
class TestRegistrationRole:
    def test_owner_registration_persists_owner(self):
        resp = _register(APIClient(), email='owner@example.com', phone='233200000001', role='OWNER')
        assert resp.status_code == status.HTTP_201_CREATED
        # Response echoes the role...
        assert resp.data['user']['role'] == 'OWNER'
        # ...and it is actually persisted in the database.
        user = User.objects.get(email='owner@example.com')
        assert user.role == User.Role.OWNER

    def test_customer_registration_persists_customer(self):
        resp = _register(APIClient(), email='cust@example.com', phone='233200000002', role='CUSTOMER')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['user']['role'] == 'CUSTOMER'
        assert User.objects.get(email='cust@example.com').role == User.Role.CUSTOMER

    def test_missing_role_defaults_to_customer(self):
        resp = _register(APIClient(), email='norole@example.com', phone='233200000003')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['user']['role'] == 'CUSTOMER'
        assert User.objects.get(email='norole@example.com').role == User.Role.CUSTOMER

    def test_admin_role_is_rejected(self):
        resp = _register(APIClient(), email='admin-try@example.com', phone='233200000004', role='ADMIN')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email='admin-try@example.com').exists()

    def test_driver_role_is_rejected(self):
        resp = _register(APIClient(), email='driver-try@example.com', phone='233200000005', role='DRIVER')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email='driver-try@example.com').exists()

    def test_owner_cannot_escalate_to_superuser_via_registration(self):
        # Mass-assignment guard: is_staff / is_superuser are not serializer fields.
        resp = _register(
            APIClient(),
            email='escalate@example.com',
            phone='233200000006',
            role='OWNER',
            is_staff=True,
            is_superuser=True,
        )
        assert resp.status_code == status.HTTP_201_CREATED
        user = User.objects.get(email='escalate@example.com')
        assert user.role == User.Role.OWNER
        assert user.is_staff is False
        assert user.is_superuser is False
