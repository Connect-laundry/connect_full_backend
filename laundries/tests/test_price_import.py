"""Tests for the AI-assisted price-list import workflow."""
import io

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from PIL import Image

from laundries.models.laundry import Laundry
from laundries.models.pricing import LaundryPricingItem
from laundries.models.price_import import PriceListImportJob
from users.models import User


def _owner(email='owner-pi@example.com', phone='233500070001'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )


def _customer(email='cust-pi@example.com', phone='233500070009'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.CUSTOMER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _laundry(owner):
    return Laundry.objects.create(
        owner=owner, name='Import Laundry', address='x', city='Accra',
        latitude='5.6', longitude='-0.18', phone_number='0240000070',
    )


def _png_image(name='pricelist.png'):
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color=(200, 100, 50)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


LIST_URL = 'dashboard-price-imports-list'
DETAIL_URL = 'dashboard-price-imports-detail'


def _confirm_url(pk):
    return reverse('dashboard-price-imports-confirm', kwargs={'pk': pk})


@pytest.mark.django_db
class TestPriceImportPermissions:
    def test_unauthenticated_denied(self):
        resp = APIClient().post(
            reverse(LIST_URL), {'source_image': _png_image()}, format='multipart'
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_customer_forbidden(self):
        resp = _client(_customer()).post(
            reverse(LIST_URL), {'source_image': _png_image()}, format='multipart'
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_without_laundry_returns_error(self):
        resp = _client(_owner()).post(
            reverse(LIST_URL), {'source_image': _png_image()}, format='multipart'
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPriceImportUpload:
    def test_upload_creates_job_with_ready_status(self):
        """NullOCRProvider extracts no items, but the job is created in READY status."""
        owner = _owner()
        _laundry(owner)
        resp = _client(owner).post(
            reverse(LIST_URL), {'source_image': _png_image()}, format='multipart'
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        data = resp.data.get('data', resp.data)
        assert data['status'] == 'READY'
        assert data['draft_items'] == []  # NullOCR returns nothing
        assert PriceListImportJob.objects.count() == 1


@pytest.mark.django_db
class TestPriceImportRetrieve:
    def test_retrieve_job_by_id(self):
        owner = _owner()
        laundry = _laundry(owner)
        job = PriceListImportJob.objects.create(
            laundry=laundry,
            source_image='test.png',
            status=PriceListImportJob.Status.READY,
            provider='null',
        )
        resp = _client(owner).get(reverse(DETAIL_URL, kwargs={'pk': job.id}))
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data.get('data', resp.data)
        assert data['id'] == str(job.id)

    def test_retrieve_nonexistent_returns_404(self):
        owner = _owner()
        _laundry(owner)
        import uuid
        resp = _client(owner).get(
            reverse(DETAIL_URL, kwargs={'pk': uuid.uuid4()})
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestPriceImportConfirm:
    def test_confirm_creates_pricing_items(self):
        owner = _owner()
        laundry = _laundry(owner)
        job = PriceListImportJob.objects.create(
            laundry=laundry,
            source_image='test.png',
            status=PriceListImportJob.Status.READY,
            provider='null',
        )
        resp = _client(owner).post(
            _confirm_url(job.id),
            {
                'items': [
                    {'item_name': 'Shirt', 'unit_price': '5.00'},
                    {'item_name': 'Trousers', 'unit_price': '8.00', 'category': 'Bottoms'},
                ]
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert LaundryPricingItem.objects.filter(laundry=laundry).count() == 2
        # Job should be confirmed
        job.refresh_from_db()
        assert job.status == PriceListImportJob.Status.CONFIRMED
        assert job.confirmed_at is not None

    def test_confirm_skips_duplicate_names(self):
        """Existing pricing items are never overwritten; duplicates are skipped."""
        owner = _owner()
        laundry = _laundry(owner)
        LaundryPricingItem.objects.create(
            laundry=laundry, item_name='Shirt', unit_price='3.00'
        )
        job = PriceListImportJob.objects.create(
            laundry=laundry,
            source_image='test.png',
            status=PriceListImportJob.Status.READY,
            provider='null',
        )
        resp = _client(owner).post(
            _confirm_url(job.id),
            {
                'items': [
                    {'item_name': 'Shirt', 'unit_price': '5.00'},  # duplicate
                    {'item_name': 'Duvet', 'unit_price': '25.00'},
                ]
            },
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data.get('data', resp.data)
        assert 'Shirt' in data['skipped']
        assert 'Duvet' in data['created']
        # Original price unchanged
        assert LaundryPricingItem.objects.get(
            laundry=laundry, item_name='Shirt'
        ).unit_price == 3.0

    def test_re_confirm_already_confirmed_job_returns_400(self):
        owner = _owner()
        laundry = _laundry(owner)
        job = PriceListImportJob.objects.create(
            laundry=laundry,
            source_image='test.png',
            status=PriceListImportJob.Status.CONFIRMED,
            provider='null',
        )
        resp = _client(owner).post(
            _confirm_url(job.id),
            {'items': [{'item_name': 'X', 'unit_price': '1.00'}]},
            format='json',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
