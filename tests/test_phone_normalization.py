"""Ghana phone numbers: however they are typed, one E.164 form."""
import pytest
from rest_framework.test import APIClient

from users.utils.phone import (
    PhoneValidationError,
    format_phone_for_display,
    mask_phone_number,
    normalize_phone,
    to_ghana_momo_account_number,
)

SAME_NUMBER = [
    '0245738120', '024 573 8120', '024-573-8120', '(024) 573 8120', '245738120',
    '233245738120', '+233245738120', '+233 24 573 8120', '00233245738120',
    '+233 0245738120', '2330245738120', '+0245738120',
]


@pytest.mark.parametrize('typed', SAME_NUMBER)
def test_every_common_spelling_normalises_to_one_number(typed):
    assert normalize_phone(typed) == '+233245738120'


@pytest.mark.parametrize('typed', ['0551057139', '+233551057139', '233551057139', '055 105 7139'])
def test_ghana_momo_account_normalization(typed):
    """Paystack Ghana recipient account_number must be 10-digit national format."""
    assert to_ghana_momo_account_number(typed) == '0551057139'
    assert mask_phone_number(typed) == '055 *** 7139'


def test_momo_account_normalization_rejects_foreign():
    with pytest.raises(PhoneValidationError):
        to_ghana_momo_account_number('+44 20 7946 0958')


@pytest.mark.parametrize('typed', ['0302123456', '+233 30 212 3456'])
def test_ghana_landlines_are_accepted(typed):
    assert normalize_phone(typed) == '+233302123456'


@pytest.mark.parametrize('bad', ['', '12345', '0145738120', '+233145738120', 'abc0245738120', '02457381201'])
def test_invalid_numbers_are_rejected(bad):
    with pytest.raises(PhoneValidationError):
        normalize_phone(bad)


def test_international_numbers_still_work():
    assert normalize_phone('+44 20 7946 0958') == '+442079460958'


def test_display_format():
    assert format_phone_for_display('+233245738120') == '+233 24 573 8120'
    assert mask_phone_number('+233245738120') == '024 *** 8120'
    assert mask_phone_number('0551057139') == '055 *** 7139'


@pytest.mark.django_db
def test_signup_stores_e164_and_blocks_the_same_number_typed_differently():
    client = APIClient()
    first = client.post('/api/v1/auth/register/', {
        'email': 'a@example.com', 'password': 'Str0ng-Pass-123', 'password_confirm': 'Str0ng-Pass-123',
        'first_name': 'A', 'last_name': 'B', 'phone': '024 573 8120'}, format='json')
    assert first.status_code == 201, first.content
    from users.models import User
    assert User.objects.get(email='a@example.com').phone == '+233245738120'
    second = client.post('/api/v1/auth/register/', {
        'email': 'b@example.com', 'password': 'Str0ng-Pass-123', 'password_confirm': 'Str0ng-Pass-123',
        'first_name': 'C', 'last_name': 'D', 'phone': '+233245738120'}, format='json')
    assert second.status_code == 400
    assert 'already exists' in str(second.content)


@pytest.mark.django_db
def test_migration_repairs_stored_numbers_but_never_merges_duplicates():
    import importlib
    from django.apps import apps
    from users.models import User
    migration = importlib.import_module('users.migrations.0015_normalize_phone_numbers')
    User.objects.create_user(email='bad-plus@example.com', phone='+0245738120', password='x-Pass-12345')
    User.objects.create_user(email='local@example.com', phone='0551234567', password='x-Pass-12345')
    User.objects.create_user(email='dup-a@example.com', phone='0209999999', password='x-Pass-12345')
    User.objects.create_user(email='dup-b@example.com', phone='+233209999999', password='x-Pass-12345')
    User.objects.create_user(email='junk@example.com', phone='12345', password='x-Pass-12345')
    migration.normalise(apps, None)
    phones = dict(User.objects.values_list('email', 'phone'))
    assert phones['bad-plus@example.com'] == '+233245738120'
    assert phones['local@example.com'] == '+233551234567'
    assert phones['dup-a@example.com'] == '0209999999'  # would collide with dup-b: left alone
    assert phones['dup-b@example.com'] == '+233209999999'
    assert phones['junk@example.com'] == '12345'
