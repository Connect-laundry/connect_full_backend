"""Tests for the owner weight-based pricing API (WeightPricingView)."""
import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.models.pricing import LaundryWeightPricing
from users.models import User


def _owner(email='owner-wp@example.com', phone='233500040001'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )


def _customer(email='cust-wp@example.com', phone='233500040009'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.CUSTOMER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _laundry(owner):
    return Laundry.objects.create(
        owner=owner, name='Weight Laundry', address='x', city='Accra',
        latitude='5.6', longitude='-0.18', phone_number='0240000030',
        pricing_model=Laundry.PricingModel.BY_WEIGHT,
    )


URL = 'dashboard-weight-pricing'


@pytest.mark.django_db
class TestWeightPricingPermissions:
    def test_unauthenticated_denied(self):
        assert APIClient().get(reverse(URL)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_customer_forbidden(self):
        assert _client(_customer()).get(reverse(URL)).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestWeightPricingCRUD:
    def test_get_when_none_configured_returns_404(self):
        owner = _owner()
        _laundry(owner)
        resp = _client(owner).get(reverse(URL))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_put_creates_weight_pricing(self):
        owner = _owner()
        _laundry(owner)
        client = _client(owner)
        resp = client.put(
            reverse(URL),
            {
                'base_price_per_kg': '15.00',
                'minimum_charge': '20.00',
                'minimum_order_weight_kg': '2.00',
                'rounding_strategy': 'UP_1_KG',
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data

    def test_get_after_creation_returns_data(self):
        owner = _owner()
        laundry = _laundry(owner)
        LaundryWeightPricing.objects.create(
            laundry=laundry, base_price_per_kg='10.00', minimum_charge='5.00',
        )
        resp = _client(owner).get(reverse(URL))
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data.get('data', resp.data)
        assert float(data['base_price_per_kg']) == 10.0

    def test_patch_partial_update(self):
        owner = _owner()
        laundry = _laundry(owner)
        LaundryWeightPricing.objects.create(
            laundry=laundry, base_price_per_kg='10.00', minimum_charge='5.00',
        )
        client = _client(owner)
        resp = client.patch(
            reverse(URL), {'base_price_per_kg': '12.50'}, format='json'
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data.get('data', resp.data)
        assert float(data['base_price_per_kg']) == 12.5
        assert float(data['minimum_charge']) == 5.0  # unchanged

    def test_put_updates_existing(self):
        owner = _owner()
        laundry = _laundry(owner)
        LaundryWeightPricing.objects.create(
            laundry=laundry, base_price_per_kg='10.00', minimum_charge='5.00',
        )
        client = _client(owner)
        resp = client.put(
            reverse(URL),
            {'base_price_per_kg': '20.00', 'minimum_charge': '10.00'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert LaundryWeightPricing.objects.filter(laundry=laundry).count() == 1

    def test_negative_base_price_rejected(self):
        owner = _owner()
        _laundry(owner)
        resp = _client(owner).put(
            reverse(URL),
            {'base_price_per_kg': '-5.00', 'minimum_charge': '0'},
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_without_laundry_returns_error(self):
        owner = _owner()
        resp = _client(owner).get(reverse(URL))
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_owner_isolation(self):
        owner_a = _owner()
        owner_b = _owner(email='owner-wp-b@example.com', phone='233500040002')
        laundry_a = _laundry(owner_a)
        LaundryWeightPricing.objects.create(
            laundry=laundry_a, base_price_per_kg='10.00', minimum_charge='5.00',
        )
        _laundry(owner_b)
        resp = _client(owner_b).get(reverse(URL))
        # owner_b has no weight pricing configured
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_rounding_strategy_choices(self):
        owner = _owner()
        _laundry(owner)
        client = _client(owner)
        for strategy in ('NONE', 'UP_0_5_KG', 'UP_1_KG'):
            resp = client.put(
                reverse(URL),
                {
                    'base_price_per_kg': '10.00',
                    'minimum_charge': '5.00',
                    'rounding_strategy': strategy,
                },
                format='json',
            )
            assert resp.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), (
                f'Failed for strategy {strategy}: {resp.data}'
            )

    def test_invalid_rounding_strategy_rejected(self):
        owner = _owner()
        _laundry(owner)
        resp = _client(owner).put(
            reverse(URL),
            {
                'base_price_per_kg': '10.00',
                'minimum_charge': '5.00',
                'rounding_strategy': 'BOGUS',
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
