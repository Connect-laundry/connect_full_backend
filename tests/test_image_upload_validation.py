from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.utils.validators import validate_file_upload
from users.models import User


def _image_bytes(fmt='PNG'):
    output = BytesIO()
    Image.new('RGB', (8, 8), color=(32, 96, 160)).save(output, format=fmt)
    return output.getvalue()


def _uploaded_image(name='avatar.png', content_type='image/png', fmt='PNG'):
    return SimpleUploadedFile(name, _image_bytes(fmt), content_type=content_type)


def _auth_client():
    user = User.objects.create_user(
        email='upload-user@example.com',
        phone='233555880001',
        password='StrongPass123!',
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.parametrize(
    ('name', 'content_type', 'fmt'),
    [
        ('photo.JPG', 'image/jpeg', 'JPEG'),
        ('photo.jpeg', 'image/jpeg', 'JPEG'),
        ('photo.png', 'image/png', 'PNG'),
        ('photo.WEBP', 'image/webp', 'WEBP'),
        ('photo', 'image/png', 'PNG'),
    ],
)
def test_image_validator_accepts_supported_images_case_insensitively(name, content_type, fmt):
    validate_file_upload(_uploaded_image(name=name, content_type=content_type, fmt=fmt))


def test_image_validator_rejects_unsupported_extension_before_storage():
    with pytest.raises(ValidationError, match='Unsupported file extension'):
        validate_file_upload(_uploaded_image(name='photo.gif', content_type='image/gif', fmt='PNG'))


def test_image_validator_rejects_mime_spoofing():
    with pytest.raises(ValidationError, match='Invalid file type'):
        validate_file_upload(_uploaded_image(name='photo.png', content_type='text/plain', fmt='PNG'))


def test_image_validator_rejects_corrupted_image():
    upload = SimpleUploadedFile('photo.png', b'not a real image', content_type='image/png')

    with pytest.raises(ValidationError, match='Invalid image file'):
        validate_file_upload(upload)


def test_image_validator_rejects_oversized_image():
    upload = SimpleUploadedFile('photo.png', b'0' * ((2 * 1024 * 1024) + 1), content_type='image/png')

    with pytest.raises(ValidationError, match='File size too large'):
        validate_file_upload(upload)


@pytest.mark.django_db
def test_laundry_model_does_not_validate_cloudinary_delivery_url_as_upload():
    owner = User.objects.create_user(
        email='cloudinary-owner@example.com',
        phone='233555880002',
        password='StrongPass123!',
        role=User.Role.OWNER,
    )
    laundry = Laundry(
        name='Cloudinary URL Laundry',
        description='Cloudinary validation test',
        address='Accra',
        city='Accra',
        latitude='5.603700',
        longitude='-0.187000',
        phone_number='0240009999',
        owner=owner,
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
        image='https://res.cloudinary.com/demo/image/upload/v1/media/laundries/abc123',
    )

    laundry.full_clean()


@pytest.mark.django_db
def test_media_upload_accepts_duplicate_supported_images(settings):
    settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
    client = _auth_client()
    url = reverse('media_upload')

    first = client.post(
        url,
        {'file': _uploaded_image(name='duplicate.PNG', content_type='image/png', fmt='PNG')},
        format='multipart',
    )
    second = client.post(
        url,
        {'file': _uploaded_image(name='duplicate.PNG', content_type='image/png', fmt='PNG')},
        format='multipart',
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.data['data']['url']
    assert second.data['data']['url']


@pytest.mark.django_db
def test_media_upload_returns_400_for_unsupported_file():
    client = _auth_client()
    response = client.post(
        reverse('media_upload'),
        {'file': SimpleUploadedFile('payload.txt', b'not an image', content_type='text/plain')},
        format='multipart',
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_profile_avatar_upload_uses_same_image_validation():
    client = _auth_client()

    response = client.patch(
        reverse('auth_me'),
        {'avatar': _uploaded_image(name='AVATAR.JPG', content_type='image/jpeg', fmt='JPEG')},
        format='multipart',
    )

    assert response.status_code == 200
    assert response.data['user']['avatar']


@pytest.mark.django_db
def test_profile_avatar_upload_rejects_corrupted_image():
    client = _auth_client()

    response = client.patch(
        reverse('auth_me'),
        {'avatar': SimpleUploadedFile('avatar.png', b'not a real image', content_type='image/png')},
        format='multipart',
    )

    assert response.status_code == 400
