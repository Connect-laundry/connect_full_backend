"""Tests for phone normalization + graceful uniqueness on PATCH /auth/me/.

Covers the "missing phone during first order" backend contract:
- Google-style user with phone=None can set a number.
- Local Ghana formats normalize to E.164.
- Invalid numbers -> 400 (not 500).
- A number already owned by another account -> 400 (not an IntegrityError 500).
- Updating with your own (unchanged) number succeeds (self-exclusion).
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from users.models import User
from users.utils.phone import normalize_phone, PhoneValidationError


# --------------------------------------------------------------------------- unit

@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('0241234567', '+233241234567'),
        ('+233241234567', '+233241234567'),
        ('233241234567', '+233241234567'),
        ('241234567', '+233241234567'),
        ('024 123 4567', '+233241234567'),
        ('024-123-4567', '+233241234567'),
        ('00233241234567', '+233241234567'),
        ('0551234567', '+233551234567'),
    ],
)
def test_normalize_ghana_numbers(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', '12345', '02412', 'abcdefg', '0141234567', '+2331234'])
def test_normalize_rejects_invalid(raw):
    with pytest.raises(PhoneValidationError):
        normalize_phone(raw)


# ---------------------------------------------------------------------- endpoint

@pytest.fixture
def google_user(db):
    # Simulate a Google/Clerk user created without a phone number.
    user = User.objects.create(
        email='google-user@example.com',
        phone=None,
        first_name='Ama',
        last_name='Mensah',
    )
    return user


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_google_user_can_set_phone_normalized(google_user):
    client = _client(google_user)
    response = client.patch(reverse('auth_me'), {'phone': '0241234567'}, format='json')
    assert response.status_code == 200
    assert response.data['user']['phone'] == '+233241234567'
    google_user.refresh_from_db()
    assert google_user.phone == '+233241234567'


@pytest.mark.django_db
def test_invalid_phone_returns_400(google_user):
    client = _client(google_user)
    response = client.patch(reverse('auth_me'), {'phone': '0141234567'}, format='json')
    assert response.status_code == 400
    google_user.refresh_from_db()
    assert google_user.phone is None


@pytest.mark.django_db
def test_duplicate_phone_returns_400_not_500(google_user):
    User.objects.create(email='owner@example.com', phone='+233241234567')
    client = _client(google_user)
    response = client.patch(reverse('auth_me'), {'phone': '0241234567'}, format='json')
    assert response.status_code == 400
    google_user.refresh_from_db()
    assert google_user.phone is None


@pytest.mark.django_db
def test_updating_with_own_number_succeeds(db):
    user = User.objects.create(email='has-phone@example.com', phone='+233241234567')
    client = _client(user)
    # Re-submitting the same number (different formatting) must not 400 on self.
    response = client.patch(reverse('auth_me'), {'phone': '0241234567'}, format='json')
    assert response.status_code == 200
    assert response.data['user']['phone'] == '+233241234567'


@pytest.mark.django_db
def test_other_fields_update_without_touching_phone(google_user):
    client = _client(google_user)
    response = client.patch(reverse('auth_me'), {'first_name': 'Kofi'}, format='json')
    assert response.status_code == 200
    google_user.refresh_from_db()
    assert google_user.first_name == 'Kofi'
    assert google_user.phone is None
