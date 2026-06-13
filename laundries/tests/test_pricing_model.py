"""Tests for the Laundry.pricing_model field across serializers."""
import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from users.models import User


def _owner(email='owner-pm@example.com', phone='233500020001'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _payload(**overrides):
    base = {
        'name': 'Pricing Model Laundry',
        'address': '1 Test Road, Accra',
        'city': 'Accra',
        'latitude': '5.603700',
        'longitude': '-0.187000',
        'phone_number': '0240000010',
        'price_range': '$$',
    }
    base.update(overrides)
    return base


@pytest.mark.django_db
class TestPricingModelField:
    def test_default_is_by_item(self):
        client = _client(_owner())
        resp = client.post(reverse('dashboard-my-laundry'), _payload(), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['data']['pricing_model'] == Laundry.PricingModel.BY_ITEM

    def test_owner_can_set_by_weight(self):
        client = _client(_owner())
        resp = client.post(
            reverse('dashboard-my-laundry'),
            _payload(pricing_model='BY_WEIGHT'),
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['data']['pricing_model'] == 'BY_WEIGHT'
        lid = resp.data['data']['id']
        assert Laundry.objects.get(id=lid).pricing_model == 'BY_WEIGHT'

    def test_hybrid_is_accepted(self):
        client = _client(_owner())
        resp = client.post(
            reverse('dashboard-my-laundry'),
            _payload(pricing_model='HYBRID'),
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['data']['pricing_model'] == 'HYBRID'

    def test_invalid_pricing_model_rejected(self):
        client = _client(_owner())
        resp = client.post(
            reverse('dashboard-my-laundry'),
            _payload(pricing_model='BOGUS'),
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert Laundry.objects.count() == 0

    def test_owner_can_patch_pricing_model(self):
        client = _client(_owner())
        created = client.post(reverse('dashboard-my-laundry'), _payload(), format='json')
        lid = created.data['data']['id']
        resp = client.patch(
            reverse('dashboard-my-laundry-detail', kwargs={'id': lid}),
            {'pricing_model': 'BY_WEIGHT'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert Laundry.objects.get(id=lid).pricing_model == 'BY_WEIGHT'
