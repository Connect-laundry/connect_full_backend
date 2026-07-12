"""Graceful-degradation tests: optional integrations failing must never 500.

Covers:
- storage/Cloudinary outages while serializing media URLs
- storage outage during media upload (controlled 503)
- Celery broker outage in request paths (password reset)
- Paystack returning malformed payloads
- missing related objects in serializer method fields
"""
from unittest import mock

import pytest
from django.core.files.storage import FileSystemStorage
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.serializers.laundry_list import LaundryListSerializer
from users.models import User
from utils.media import safe_media_url
from utils.tasks import safe_task_delay


class _BrokenStorageError(Exception):
    """Deliberately not ValueError/AttributeError — simulates a Cloudinary
    misconfiguration/connection error that DRF's stock handling misses."""


@pytest.fixture
def owner_client(db):
    owner = User.objects.create_user(
        email='resilience-owner@example.com',
        phone='233555700001',
        password='StrongPass123!',
        role='OWNER',
    )
    client = APIClient()
    client.force_authenticate(user=owner)
    return client, owner


@pytest.fixture
def laundry_with_image(db, owner_client):
    _, owner = owner_client
    return Laundry.objects.create(
        name='Broken Image Laundry',
        owner=owner,
        address='Addr',
        latitude=5.6,
        longitude=-0.1,
        phone_number='0123456789',
        # Bypass upload validation — simulates a previously-persisted file.
        image='laundries/persisted.jpg',
    )


@pytest.fixture
def broken_storage():
    """Make every FileSystemStorage.url call raise a storage-layer error."""
    with mock.patch.object(
        FileSystemStorage, 'url', side_effect=_BrokenStorageError('storage down')
    ):
        yield


# ---------------------------------------------------------------- safe_media_url

def test_safe_media_url_returns_none_for_empty_file():
    assert safe_media_url(None) is None
    assert safe_media_url('') is None


def test_safe_media_url_swallows_arbitrary_storage_errors():
    file = mock.Mock()
    file.__bool__ = lambda self: True
    type(file).url = mock.PropertyMock(side_effect=_BrokenStorageError('boom'))
    assert safe_media_url(file) is None


def test_safe_media_url_builds_absolute_uri():
    file = mock.Mock()
    file.__bool__ = lambda self: True
    type(file).url = mock.PropertyMock(return_value='/media/x.jpg')
    request = mock.Mock()
    request.build_absolute_uri.return_value = 'http://testserver/media/x.jpg'
    assert safe_media_url(file, request) == 'http://testserver/media/x.jpg'


# ------------------------------------------------------- endpoint degradation

@pytest.mark.django_db
def test_my_laundry_endpoint_survives_storage_outage(owner_client, laundry_with_image, broken_storage):
    client, _ = owner_client
    response = client.get('/api/v1/laundries/dashboard/my-laundry/')
    assert response.status_code == 200
    assert response.data['data']['imageUrl'] is None
    assert response.data['data']['image'] is None


@pytest.mark.django_db
def test_laundry_list_serializer_survives_storage_outage(laundry_with_image, broken_storage):
    data = LaundryListSerializer(laundry_with_image).data
    assert data['imageUrl'] is None
    assert data['image'] is None


@pytest.mark.django_db
def test_media_upload_returns_503_when_storage_unavailable(auth_client):
    from io import BytesIO
    from PIL import Image
    from django.core.files.uploadedfile import SimpleUploadedFile

    output = BytesIO()
    Image.new('RGB', (8, 8)).save(output, format='PNG')
    upload = SimpleUploadedFile('x.png', output.getvalue(), content_type='image/png')

    with mock.patch(
        'users.views.media.default_storage.save',
        side_effect=_BrokenStorageError('cloudinary unreachable'),
    ):
        response = auth_client.post(
            '/api/v1/media/upload/', {'file': upload}, format='multipart'
        )
    assert response.status_code == 503
    assert response.data['status'] == 'error'


# --------------------------------------------------------------- safe_task_delay

def test_safe_task_delay_returns_false_when_broker_down():
    task = mock.Mock()
    task.name = 'test.task'
    task.delay.side_effect = ConnectionError('broker down')
    assert safe_task_delay(task, 'arg') is False
    task.apply.assert_not_called()


def test_safe_task_delay_falls_back_to_sync():
    task = mock.Mock()
    task.name = 'test.task'
    task.delay.side_effect = ConnectionError('broker down')
    assert safe_task_delay(task, 'arg', fallback_sync=True, kw=1) is True
    task.apply.assert_called_once_with(args=('arg',), kwargs={'kw': 1})


def test_safe_task_delay_queues_normally():
    task = mock.Mock()
    task.name = 'test.task'
    assert safe_task_delay(task, 'arg') is True
    task.delay.assert_called_once_with('arg')


@pytest.mark.django_db
def test_forgot_password_survives_broker_outage(api_client):
    User.objects.create_user(
        email='reset-me@example.com',
        phone='233555700002',
        password='StrongPass123!',
    )
    with mock.patch(
        'users.views.password_reset.send_password_reset_email'
    ) as task:
        task.delay.side_effect = ConnectionError('broker down')
        task.apply.return_value = None  # sync fallback succeeds
        response = api_client.post(
            '/api/v1/auth/forgot-password/', {'email': 'reset-me@example.com'}
        )
    assert response.status_code == 200
    task.apply.assert_called_once()


@pytest.mark.django_db
def test_forgot_password_survives_total_email_failure(api_client):
    User.objects.create_user(
        email='reset-me2@example.com',
        phone='233555700003',
        password='StrongPass123!',
    )
    with mock.patch(
        'users.views.password_reset.send_password_reset_email'
    ) as task:
        task.delay.side_effect = ConnectionError('broker down')
        task.apply.side_effect = ConnectionError('smtp down')
        response = api_client.post(
            '/api/v1/auth/forgot-password/', {'email': 'reset-me2@example.com'}
        )
    # Anti-enumeration response is 200 regardless; the failure is logged.
    assert response.status_code == 200


# -------------------------------------------------------------------- Paystack

@pytest.mark.django_db
def test_payment_initialize_handles_malformed_paystack_payload(auth_client, sample_order):
    """Paystack replying status=true with missing checkout fields must not 500."""
    with mock.patch(
        'payments.views.PaystackService.initialize_transaction',
        return_value={'status': True, 'data': {}},
    ):
        response = auth_client.post(
            '/api/v1/payments/initialize/',
            {'order_id': str(sample_order.id), 'payment_method': 'CARD'},
        )
    assert response.status_code == 400
    assert response.data['status'] == 'error'


@pytest.mark.django_db
def test_payment_initialize_handles_paystack_timeout(auth_client, sample_order):
    with mock.patch(
        'payments.views.PaystackService.initialize_transaction',
        return_value={'status': False, 'message': 'Connection timed out'},
    ):
        response = auth_client.post(
            '/api/v1/payments/initialize/',
            {'order_id': str(sample_order.id), 'payment_method': 'CARD'},
        )
    assert response.status_code == 400


@pytest.mark.django_db
def test_payment_verify_handles_malformed_payload(auth_client):
    with mock.patch(
        'payments.views.PaystackService.verify_transaction',
        return_value={'status': True},  # no 'data' key at all
    ):
        response = auth_client.get('/api/v1/payments/verify/REF-DOES-NOT-EXIST/')
    assert response.status_code == 400


# --------------------------------------------------- missing-relation hardening

@pytest.mark.django_db
def test_laundry_detail_is_favorite_without_request_context(laundry_with_image):
    from laundries.serializers.laundry_detail import LaundryDetailSerializer
    # No request in context (e.g. background serialization) must not raise.
    data = LaundryDetailSerializer(laundry_with_image, context={}).data
    assert data['isFavorite'] is False
