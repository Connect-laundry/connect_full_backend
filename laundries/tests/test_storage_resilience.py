"""Regression tests: storage-write failures must degrade, never 500.

Reproduces the production incident where set-but-invalid Cloudinary credentials
select the Cloudinary backend and every media *write* raises, taking down
onboarding (POST my-laundry) with an unhandled 500.

The test settings use ``FileSystemStorage``; we simulate any storage backend
failing by patching its low-level ``_save`` so it raises — proving the guards
catch storage errors regardless of the concrete backend or exception type.
"""
import io
from contextlib import contextmanager
from unittest import mock

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.models.price_import import PriceListImportJob
from laundries.models.pricing import LaundryPricingItem
from users.models import User


# --- fixtures / helpers ----------------------------------------------------

class _FakeCloudinaryError(Exception):
    """Stand-in for cloudinary.exceptions.Error (not importable in tests)."""


@contextmanager
def failing_storage(exc=None):
    """Make every storage write raise, simulating a broken/misconfigured backend."""
    exc = exc or OSError('simulated storage outage')

    def _boom(*args, **kwargs):
        raise exc

    with mock.patch.object(FileSystemStorage, '_save', side_effect=_boom):
        yield


def _png(name='shop.png'):
    from PIL import Image

    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color=(20, 120, 200)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def _owner(email='owner-store@example.com', phone='233500000801'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )


def _customer(email='cust-store@example.com', phone='233500000802'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.CUSTOMER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _laundry(owner, name='Sunshine'):
    return Laundry.objects.create(
        name=name, owner=owner, address='12 Ring Rd, Accra',
        latitude='5.603700', longitude='-0.187000', phone_number='0240000001',
    )


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
    }


LIST_URL = 'dashboard-my-laundry'
DETAIL_URL = 'dashboard-my-laundry-detail'


# --- the reported bug: onboarding create -----------------------------------

@pytest.mark.django_db
class TestMyLaundryCreateStorageResilience:
    def test_create_with_image_survives_storage_failure(self):
        """Optional logo + broken storage => 201, laundry saved, no logo."""
        owner = _owner()
        payload = _base_payload()
        payload['image'] = _png()

        with failing_storage():
            resp = _client(owner).post(reverse(LIST_URL), payload, format='multipart')

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        data = resp.data['data']
        assert data['imageUrl'] is None
        # The registration itself must persist despite the storage outage.
        laundry = Laundry.objects.get(id=data['id'])
        assert laundry.owner_id == owner.id
        assert not laundry.image

    def test_create_without_image_unaffected(self):
        owner = _owner()
        with failing_storage():
            resp = _client(owner).post(reverse(LIST_URL), _base_payload(), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['data']['imageUrl'] is None

    def test_create_with_image_storage_healthy_still_stores(self):
        owner = _owner()
        payload = _base_payload()
        payload['image'] = _png()
        resp = _client(owner).post(reverse(LIST_URL), payload, format='multipart')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['data']['imageUrl'] is not None
        assert Laundry.objects.get(id=resp.data['data']['id']).image

    @pytest.mark.parametrize('exc', [
        OSError('disk gone'),
        ConnectionError('connection refused'),
        TimeoutError('timed out'),
        ValueError('invalid credentials'),
        _FakeCloudinaryError('Invalid api_key'),
    ])
    def test_create_degrades_for_any_storage_exception(self, exc):
        owner = _owner()
        payload = _base_payload()
        payload['image'] = _png()
        with failing_storage(exc):
            resp = _client(owner).post(reverse(LIST_URL), payload, format='multipart')
        assert resp.status_code == status.HTTP_201_CREATED, (exc, resp.data)
        assert resp.data['data']['imageUrl'] is None
        assert Laundry.objects.count() == 1


@pytest.mark.django_db
class TestMyLaundryUpdateStorageResilience:
    def test_patch_new_logo_storage_failure_keeps_registration(self):
        owner = _owner()
        laundry = _laundry(owner)
        with failing_storage():
            resp = _client(owner).patch(
                reverse(DETAIL_URL, kwargs={'id': laundry.id}),
                {'image': _png(), 'name': 'Renamed'},
                format='multipart',
            )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        laundry.refresh_from_db()
        # Non-media fields still updated; logo write degraded, no 500.
        assert laundry.name == 'Renamed'
        assert not laundry.image


# --- required file: OCR price-import must 503, not 500 ----------------------

@pytest.mark.django_db
class TestPriceImportStorageResilience:
    URL = 'dashboard-price-imports-list'

    def test_storage_failure_returns_503_and_no_orphan_job(self):
        owner = _owner()
        _laundry(owner)
        with failing_storage():
            resp = _client(owner).post(
                reverse(self.URL), {'source_image': _png('list.png')}, format='multipart'
            )
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, resp.data
        # The transaction rolled back — no half-created job left behind.
        assert PriceListImportJob.objects.count() == 0


# --- optional media across other write paths -------------------------------

@pytest.mark.django_db
class TestPricingItemStorageResilience:
    URL = 'dashboard-pricing-items-list'

    def test_item_create_with_image_survives_storage_failure(self):
        owner = _owner()
        _laundry(owner)
        with failing_storage():
            resp = _client(owner).post(
                reverse(self.URL),
                {'item_name': 'Shirt', 'unit_price': '5.00', 'image': _png('shirt.png')},
                format='multipart',
            )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        item = LaundryPricingItem.objects.get()
        assert item.item_name == 'Shirt'
        assert not item.image


@pytest.mark.django_db
class TestAvatarStorageResilience:
    URL = 'auth_me'

    def test_avatar_update_survives_storage_failure(self):
        user = _customer()
        with failing_storage():
            resp = _client(user).patch(
                reverse(self.URL), {'avatar': _png('me.png')}, format='multipart'
            )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        user.refresh_from_db()
        assert not user.avatar


@pytest.mark.django_db
class TestMediaUploadStorageResilience:
    URL = 'media_upload'

    def test_upload_returns_503_on_storage_failure(self):
        user = _customer()
        with failing_storage():
            resp = _client(user).post(
                reverse(self.URL), {'file': _png('x.png'), 'folder': 'uploads'},
                format='multipart',
            )
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, resp.data

    def test_upload_succeeds_when_storage_healthy(self):
        user = _customer()
        resp = _client(user).post(
            reverse(self.URL), {'file': _png('x.png'), 'folder': 'uploads'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert resp.data['data']['url']
