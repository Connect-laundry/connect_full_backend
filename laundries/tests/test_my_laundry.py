"""End-to-end tests for the owner 'My Laundry' feature and OWNER registration."""
import io
import json

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.models.opening_hours import OpeningHours
from users.models import User


def _owner(email='owner-ml@example.com', phone='233500000001'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )


def _customer(email='cust-ml@example.com', phone='233500000002'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.CUSTOMER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _png_upload(name='shop.png'):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color=(20, 120, 200)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def _base_payload():
    return {
        'name': 'Sunshine Laundry',
        'description': 'Fast and fresh',
        'address': '12 Ring Road, Accra',
        'city': 'Accra',
        'latitude': '5.603700',
        'longitude': '-0.187000',
        'phone_number': '0240000001',
        'price_range': '$$',
        'estimated_delivery_hours': 24,
        'delivery_fee': '5.00',
        'pickup_fee': '2.00',
        'min_order': '10.00',
    }


def _hours():
    return [
        {'day': 1, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
        {'day': 2, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
        {'day': 7, 'is_closed': True},
    ]


LIST_URL = 'dashboard-my-laundry'
DETAIL_URL = 'dashboard-my-laundry-detail'


@pytest.mark.django_db
class TestMyLaundryAuth:
    def test_unauthenticated_denied(self):
        resp = APIClient().get(reverse(LIST_URL))
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_customer_forbidden(self):
        resp = _client(_customer()).get(reverse(LIST_URL))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_without_laundry_gets_404(self):
        resp = _client(_owner()).get(reverse(LIST_URL))
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert resp.data['status'] == 'error'


@pytest.mark.django_db
class TestMyLaundryCreate:
    def test_create_multipart_with_image_and_hours(self):
        owner = _owner()
        client = _client(owner)
        payload = _base_payload()
        payload['image'] = _png_upload()
        payload['operating_hours'] = json.dumps(_hours())

        resp = client.post(reverse(LIST_URL), payload, format='multipart')

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        data = resp.data['data']
        assert data['name'] == 'Sunshine Laundry'
        # Forced platform-controlled fields.
        assert data['status'] == Laundry.ApprovalStatus.PENDING
        assert data['is_active'] is False
        assert data['is_featured'] is False
        assert data['imageUrl'] is not None
        assert len(data['operating_hours']) == 3

        laundry = Laundry.objects.get(id=data['id'])
        assert laundry.owner_id == owner.id
        assert OpeningHours.objects.filter(laundry=laundry).count() == 3
        closed = OpeningHours.objects.get(laundry=laundry, day=7)
        assert closed.is_closed is True

    def test_create_json_without_image(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = _hours()  # native list (JSON request)
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['data']['imageUrl'] is None

    def test_owner_field_cannot_be_spoofed(self):
        owner = _owner()
        victim = _owner(email='victim@example.com', phone='233500000099')
        payload = _base_payload()
        payload['owner'] = str(victim.id)
        resp = _client(owner).post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert Laundry.objects.get(id=resp.data['data']['id']).owner_id == owner.id

    def test_readonly_status_ignored_on_create(self):
        payload = _base_payload()
        payload['status'] = 'APPROVED'
        payload['is_active'] = True
        payload['is_featured'] = True
        resp = _client(_owner()).post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.data['data']
        assert data['status'] == 'PENDING'
        assert data['is_active'] is False
        assert data['is_featured'] is False

    def test_duplicate_creation_blocked(self):
        client = _client(_owner())
        first = client.post(reverse(LIST_URL), _base_payload(), format='json')
        assert first.status_code == status.HTTP_201_CREATED
        second = client.post(reverse(LIST_URL), _base_payload(), format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert 'already' in second.data['message'].lower()
        assert Laundry.objects.filter(owner__email='owner-ml@example.com').count() == 1

    def test_invalid_operating_hours_json_rejected(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = '{not valid json'
        resp = client.post(reverse(LIST_URL), payload, format='multipart')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert Laundry.objects.count() == 0  # rolled back / never created

    def test_missing_required_fields_rejected(self):
        client = _client(_owner())
        resp = client.post(reverse(LIST_URL), {'description': 'no name'}, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert Laundry.objects.count() == 0

    def test_open_day_with_bad_time_range_rolls_back(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = json.dumps(
            [{'day': 1, 'opening_time': '18:00', 'closing_time': '08:00', 'is_closed': False}]
        )
        resp = client.post(reverse(LIST_URL), payload, format='multipart')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        # Transaction rollback: no orphan laundry or hours.
        assert Laundry.objects.count() == 0
        assert OpeningHours.objects.count() == 0


@pytest.mark.django_db
class TestMyLaundryGet:
    def test_get_returns_owner_laundry_with_hours(self):
        owner = _owner()
        client = _client(owner)
        payload = _base_payload()
        payload['operating_hours'] = _hours()
        created = client.post(reverse(LIST_URL), payload, format='json')
        assert created.status_code == status.HTTP_201_CREATED

        resp = client.get(reverse(LIST_URL))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['data']['name'] == 'Sunshine Laundry'
        assert len(resp.data['data']['operating_hours']) == 3


@pytest.mark.django_db
class TestMyLaundryPatch:
    def _create(self, client):
        payload = _base_payload()
        payload['operating_hours'] = _hours()
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        return resp.data['data']['id']

    def test_partial_update_name(self):
        client = _client(_owner())
        lid = self._create(client)
        resp = client.patch(
            reverse(DETAIL_URL, kwargs={'id': lid}),
            {'name': 'Renamed Laundry'},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['data']['name'] == 'Renamed Laundry'
        assert Laundry.objects.get(id=lid).name == 'Renamed Laundry'

    def test_readonly_fields_ignored_on_patch(self):
        client = _client(_owner())
        lid = self._create(client)
        resp = client.patch(
            reverse(DETAIL_URL, kwargs={'id': lid}),
            {'status': 'APPROVED', 'is_active': True, 'is_featured': True},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        laundry = Laundry.objects.get(id=lid)
        assert laundry.status == 'PENDING'
        assert laundry.is_active is False
        assert laundry.is_featured is False

    def test_patch_updates_operating_hours_diff(self):
        client = _client(_owner())
        lid = self._create(client)
        # Replace with just Monday open + Tuesday closed -> day 7 removed.
        new_hours = [
            {'day': 1, 'opening_time': '09:00', 'closing_time': '17:00', 'is_closed': False},
            {'day': 2, 'is_closed': True},
        ]
        resp = client.patch(
            reverse(DETAIL_URL, kwargs={'id': lid}),
            {'operating_hours': new_hours},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        days = set(OpeningHours.objects.filter(laundry_id=lid).values_list('day', flat=True))
        assert days == {1, 2}
        monday = OpeningHours.objects.get(laundry_id=lid, day=1)
        assert monday.opening_time.strftime('%H:%M') == '09:00'

    def test_cannot_update_another_owners_laundry(self):
        owner_a = _owner()
        owner_b = _owner(email='owner-b@example.com', phone='233500000003')
        lid = self._create(_client(owner_a))
        resp = _client(owner_b).patch(
            reverse(DETAIL_URL, kwargs={'id': lid}),
            {'name': 'Hijacked'},
            format='json',
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert Laundry.objects.get(id=lid).name == 'Sunshine Laundry'

    def test_customer_cannot_patch(self):
        lid = self._create(_client(_owner()))
        resp = _client(_customer()).patch(
            reverse(DETAIL_URL, kwargs={'id': lid}),
            {'name': 'Nope'},
            format='json',
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestOwnerRegistration:
    def test_register_as_owner_persists_owner_role(self):
        resp = APIClient().post(
            reverse('auth_register'),
            {
                'email': 'newowner@example.com',
                'phone': '233500000010',
                'first_name': 'New',
                'last_name': 'Owner',
                'password': 'StrongPass123!',
                'password_confirm': 'StrongPass123!',
                'role': 'OWNER',
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['user']['role'] == 'OWNER'
        assert User.objects.get(email='newowner@example.com').role == User.Role.OWNER

    def test_register_defaults_to_customer(self):
        resp = APIClient().post(
            reverse('auth_register'),
            {
                'email': 'defaultrole@example.com',
                'phone': '233500000011',
                'password': 'StrongPass123!',
                'password_confirm': 'StrongPass123!',
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert User.objects.get(email='defaultrole@example.com').role == User.Role.CUSTOMER

    def test_cannot_self_register_as_admin(self):
        resp = APIClient().post(
            reverse('auth_register'),
            {
                'email': 'wannabeadmin@example.com',
                'phone': '233500000012',
                'password': 'StrongPass123!',
                'password_confirm': 'StrongPass123!',
                'role': 'ADMIN',
            },
            format='json',
        )
        # Rejected by the ChoiceField; never persisted as ADMIN.
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.filter(email='wannabeadmin@example.com').exists()
