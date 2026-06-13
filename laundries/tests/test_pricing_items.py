"""Tests for the owner per-item pricing catalog (LaundryPricingItem)."""
import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.models.pricing import LaundryPricingItem
from users.models import User


def _owner(email='owner-pi@example.com', phone='233500030001'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )


def _customer(email='cust-pi@example.com', phone='233500030009'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.CUSTOMER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _laundry(owner):
    return Laundry.objects.create(
        owner=owner, name='Cat Laundry', address='x', city='Accra',
        latitude='5.6', longitude='-0.18', phone_number='0240000020',
    )


LIST_URL = 'dashboard-pricing-items-list'
DETAIL_URL = 'dashboard-pricing-items-detail'
BULK_UPDATE_URL = 'dashboard-pricing-items-bulk-update'
REORDER_URL = 'dashboard-pricing-items-bulk-reorder'


@pytest.mark.django_db
class TestPricingItemPermissions:
    def test_unauthenticated_denied(self):
        assert APIClient().get(reverse(LIST_URL)).status_code == status.HTTP_401_UNAUTHORIZED

    def test_customer_forbidden(self):
        assert _client(_customer()).get(reverse(LIST_URL)).status_code == status.HTTP_403_FORBIDDEN

    def test_create_without_laundry_blocked(self):
        resp = _client(_owner()).post(
            reverse(LIST_URL), {'item_name': 'Shirt', 'unit_price': '5.00'}, format='json'
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPricingItemCRUD:
    def test_create_and_list(self):
        owner = _owner()
        _laundry(owner)
        client = _client(owner)
        resp = client.post(
            reverse(LIST_URL),
            {'item_name': 'Shirt', 'unit_price': '5.00', 'category': 'Tops'},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        # StandardResponseRenderer wraps in {status, message, data}
        item_data = resp.data.get('data', resp.data)
        # item_data may itself be the envelope or the raw serializer output
        if isinstance(item_data, dict) and 'item_name' in item_data:
            assert item_data['item_name'] == 'Shirt'
        else:
            assert resp.data['item_name'] == 'Shirt'

        listing = client.get(reverse(LIST_URL))
        assert listing.status_code == status.HTTP_200_OK
        list_data = listing.data['data'] if isinstance(listing.data, dict) else listing.data
        assert len(list_data) == 1

    def test_unit_price_cannot_be_negative(self):
        owner = _owner()
        _laundry(owner)
        resp = _client(owner).post(
            reverse(LIST_URL), {'item_name': 'Shirt', 'unit_price': '-1.00'}, format='json'
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_duplicate_item_name_rejected(self):
        owner = _owner()
        _laundry(owner)
        client = _client(owner)
        first = client.post(reverse(LIST_URL), {'item_name': 'Shirt', 'unit_price': '5'}, format='json')
        assert first.status_code == status.HTTP_201_CREATED
        dup = client.post(reverse(LIST_URL), {'item_name': 'Shirt', 'unit_price': '6'}, format='json')
        assert dup.status_code == status.HTTP_400_BAD_REQUEST
        assert 'already exists' in dup.data.get('message', '')

    def test_update_and_delete(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)
        item = LaundryPricingItem.objects.create(laundry=laundry, item_name='Duvet', unit_price='20')
        patch = client.patch(
            reverse(DETAIL_URL, kwargs={'pk': item.id}), {'unit_price': '25.00'}, format='json'
        )
        assert patch.status_code == status.HTTP_200_OK
        assert LaundryPricingItem.objects.get(id=item.id).unit_price == 25
        delete = client.delete(reverse(DETAIL_URL, kwargs={'pk': item.id}))
        assert delete.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT)
        assert LaundryPricingItem.objects.filter(id=item.id).count() == 0

    def test_owner_isolation(self):
        owner_a = _owner()
        owner_b = _owner(email='owner-pi-b@example.com', phone='233500030002')
        laundry_a = _laundry(owner_a)
        item = LaundryPricingItem.objects.create(laundry=laundry_a, item_name='Shirt', unit_price='5')
        _laundry(owner_b)
        # owner_b cannot see or fetch owner_a's item
        listing = _client(owner_b).get(reverse(LIST_URL))
        list_data = listing.data['data'] if isinstance(listing.data, dict) else listing.data
        assert list_data == []
        fetch = _client(owner_b).get(reverse(DETAIL_URL, kwargs={'pk': item.id}))
        assert fetch.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestPricingItemBulk:
    def test_bulk_update(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)
        a = LaundryPricingItem.objects.create(laundry=laundry, item_name='A', unit_price='1')
        b = LaundryPricingItem.objects.create(laundry=laundry, item_name='B', unit_price='2')
        resp = client.post(
            reverse(BULK_UPDATE_URL),
            {'items': [
                {'id': str(a.id), 'unit_price': '9.00', 'is_active': False},
                {'id': str(b.id), 'item_name': 'B2'},
            ]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        a.refresh_from_db(); b.refresh_from_db()
        assert a.unit_price == 9 and a.is_active is False
        assert b.item_name == 'B2'

    def test_bulk_update_rejects_foreign_id(self):
        owner = _owner()
        other = _owner(email='owner-pi-c@example.com', phone='233500030003')
        laundry = _laundry(owner)
        foreign = LaundryPricingItem.objects.create(
            laundry=_laundry(other), item_name='X', unit_price='1'
        )
        resp = _client(owner).post(
            reverse(BULK_UPDATE_URL),
            {'items': [{'id': str(foreign.id), 'unit_price': '99'}]},
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        foreign.refresh_from_db()
        assert foreign.unit_price == 1

    def test_bulk_reorder(self):
        owner = _owner()
        laundry = _laundry(owner)
        client = _client(owner)
        a = LaundryPricingItem.objects.create(laundry=laundry, item_name='A', unit_price='1', display_order=0)
        b = LaundryPricingItem.objects.create(laundry=laundry, item_name='B', unit_price='2', display_order=1)
        resp = client.post(
            reverse(REORDER_URL),
            {'items': [{'id': str(a.id), 'display_order': 5}, {'id': str(b.id), 'display_order': 1}]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        a.refresh_from_db()
        assert a.display_order == 5
