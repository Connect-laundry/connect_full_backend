"""End-to-end owner onboarding integration test.

Simulates the full owner lifecycle:
  Register → create laundry → choose pricing model → configure hours
  → configure location → create pricing items → fetch & verify → edit & verify.
"""
import json

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.models.opening_hours import OpeningHours
from laundries.models.pricing import LaundryPricingItem, LaundryWeightPricing
from users.models import User, DeviceSession, SessionRefreshToken


@pytest.mark.django_db(transaction=True)
class TestOwnerOnboardingIntegration:
    """Full end-to-end owner onboarding simulation."""

    def test_full_onboarding_flow(self):
        client = APIClient()

        # ── Step 1: Register as OWNER ────────────────────────────────
        reg_resp = client.post(
            reverse('auth_register'),
            {
                'email': 'integration-owner@example.com',
                'phone': '233500080001',
                'first_name': 'Kofi',
                'last_name': 'Owusu',
                'password': 'StrongPass123!',
                'password_confirm': 'StrongPass123!',
                'role': 'OWNER',
            },
            format='json',
        )
        assert reg_resp.status_code == status.HTTP_201_CREATED, reg_resp.data
        assert reg_resp.data['accessToken']
        assert reg_resp.data['refreshToken']
        user_id = reg_resp.data['user']['id']
        assert reg_resp.data['user']['role'] == 'OWNER'

        user = User.objects.get(id=user_id)
        assert user.role == User.Role.OWNER
        assert DeviceSession.objects.filter(user=user).count() == 1
        assert SessionRefreshToken.objects.filter(session__user=user).count() == 1

        # Authenticate all subsequent requests
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {reg_resp.data["accessToken"]}'
        )

        # ── Step 2: Create laundry with operating hours ──────────────
        laundry_payload = {
            'name': 'Kofi Premium Wash',
            'description': 'Professional laundry service',
            'address': '15 Ring Road East, Accra',
            'city': 'Accra',
            'phone_number': '0240000080',
            'price_range': '$$',
            'pricing_model': 'BY_ITEM',
            'estimated_delivery_hours': 24,
            'delivery_fee': '5.00',
            'pickup_fee': '2.50',
            'min_order': '10.00',
            'location': {
                'latitude': '5.603700',
                'longitude': '-0.187000',
                'method': 'gps',
            },
            'operating_hours': [
                {'day': 1, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
                {'day': 2, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
                {'day': 3, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
                {'day': 4, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
                {'day': 5, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
                {'day': 6, 'opening_time': '09:00', 'closing_time': '15:00', 'is_closed': False},
                {'day': 7, 'is_closed': True},
            ],
        }
        create_resp = client.post(
            reverse('dashboard-my-laundry'), laundry_payload, format='json'
        )
        assert create_resp.status_code == status.HTTP_201_CREATED, create_resp.data
        laundry_data = create_resp.data['data']
        laundry_id = laundry_data['id']

        # Verify platform-controlled fields
        assert laundry_data['status'] == 'PENDING'
        assert laundry_data['is_active'] is False
        assert laundry_data['pricing_model'] == 'BY_ITEM'
        assert len(laundry_data['operating_hours']) == 7
        assert float(laundry_data['latitude']) == pytest.approx(5.6037, abs=0.001)

        # ── Step 3: Verify laundry fetch ─────────────────────────────
        get_resp = client.get(reverse('dashboard-my-laundry'))
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.data['data']['name'] == 'Kofi Premium Wash'
        assert len(get_resp.data['data']['operating_hours']) == 7

        # ── Step 4: Create pricing items ─────────────────────────────
        pricing_url = reverse('dashboard-pricing-items-list')
        items_to_create = [
            {'item_name': 'Shirt', 'unit_price': '5.00', 'category': 'Tops'},
            {'item_name': 'Trousers', 'unit_price': '7.00', 'category': 'Bottoms'},
            {'item_name': 'Duvet', 'unit_price': '25.00', 'category': 'Bedding'},
        ]
        for item in items_to_create:
            resp = client.post(pricing_url, item, format='json')
            assert resp.status_code == status.HTTP_201_CREATED, resp.data

        # Verify items list
        list_resp = client.get(pricing_url)
        assert list_resp.status_code == status.HTTP_200_OK
        list_data = list_resp.data['data'] if isinstance(list_resp.data, dict) else list_resp.data
        assert len(list_data) == 3

        # ── Step 5: Duplicate pricing item handled gracefully ────────
        dup_resp = client.post(
            pricing_url,
            {'item_name': 'Shirt', 'unit_price': '6.00'},
            format='json',
        )
        assert dup_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert LaundryPricingItem.objects.filter(
            laundry_id=laundry_id, item_name='Shirt'
        ).count() == 1

        # ── Step 6: Edit laundry — change pricing model ──────────────
        patch_resp = client.patch(
            reverse('dashboard-my-laundry-detail', kwargs={'id': laundry_id}),
            {'pricing_model': 'BY_WEIGHT', 'name': 'Kofi Express Wash'},
            format='json',
        )
        assert patch_resp.status_code == status.HTTP_200_OK
        assert patch_resp.data['data']['pricing_model'] == 'BY_WEIGHT'
        assert patch_resp.data['data']['name'] == 'Kofi Express Wash'
        assert Laundry.objects.get(id=laundry_id).pricing_model == 'BY_WEIGHT'

        # ── Step 7: Configure weight pricing ─────────────────────────
        weight_resp = client.put(
            reverse('dashboard-weight-pricing'),
            {
                'base_price_per_kg': '12.00',
                'minimum_charge': '15.00',
                'minimum_order_weight_kg': '1.00',
                'rounding_strategy': 'UP_0_5_KG',
            },
            format='json',
        )
        assert weight_resp.status_code == status.HTTP_201_CREATED, weight_resp.data

        # Verify weight pricing persisted
        weight_get = client.get(reverse('dashboard-weight-pricing'))
        assert weight_get.status_code == status.HTTP_200_OK
        weight_data = weight_get.data.get('data', weight_get.data)
        assert float(weight_data['base_price_per_kg']) == 12.0

        # ── Step 8: Update operating hours ───────────────────────────
        new_hours = [
            {'day': 1, 'opening_time': '07:00', 'closing_time': '20:00', 'is_closed': False},
            {'day': 7, 'is_closed': True},
        ]
        hours_resp = client.patch(
            reverse('dashboard-my-laundry-detail', kwargs={'id': laundry_id}),
            {'operating_hours': new_hours},
            format='json',
        )
        assert hours_resp.status_code == status.HTTP_200_OK
        # Only 2 days should remain
        assert OpeningHours.objects.filter(laundry_id=laundry_id).count() == 2

        # ── Step 9: Final verification ───────────────────────────────
        final = client.get(reverse('dashboard-my-laundry'))
        assert final.status_code == status.HTTP_200_OK
        final_data = final.data['data']
        assert final_data['name'] == 'Kofi Express Wash'
        assert final_data['pricing_model'] == 'BY_WEIGHT'
        assert len(final_data['operating_hours']) == 2

        # DB-level verification
        laundry = Laundry.objects.get(id=laundry_id)
        assert laundry.owner == user
        assert laundry.pricing_model == 'BY_WEIGHT'
        assert LaundryPricingItem.objects.filter(laundry=laundry).count() == 3
        assert LaundryWeightPricing.objects.filter(laundry=laundry).count() == 1


@pytest.mark.django_db(transaction=True)
class TestRegistrationIdempotency:
    """Duplicate registration attempts should be handled safely."""

    def test_duplicate_registration_email_rejected(self):
        payload = {
            'email': 'dup-test@example.com',
            'phone': '233500080010',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
            'role': 'OWNER',
        }
        first = APIClient().post(reverse('auth_register'), payload, format='json')
        assert first.status_code == status.HTTP_201_CREATED

        second = APIClient().post(reverse('auth_register'), payload, format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert User.objects.filter(email='dup-test@example.com').count() == 1

    def test_duplicate_laundry_creation_blocked(self):
        user = User.objects.create_user(
            email='dup-laundry@example.com', phone='233500080020',
            password='StrongPass123!', role=User.Role.OWNER,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        payload = {
            'name': 'Dup Laundry',
            'address': 'Addr',
            'city': 'Accra',
            'latitude': '5.6',
            'longitude': '-0.18',
            'phone_number': '0240000099',
            'price_range': '$$',
        }
        first = client.post(reverse('dashboard-my-laundry'), payload, format='json')
        assert first.status_code == status.HTTP_201_CREATED

        second = client.post(reverse('dashboard-my-laundry'), payload, format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert Laundry.objects.filter(owner=user).count() == 1
