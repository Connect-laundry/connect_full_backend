import pytest
from users.services.clerk_service import _is_valid_email_str, _profile_from_claims, _primary_email
from users.serializers.profile import ProfileSerializer
from users.models import User

def test_is_valid_email_str():
    assert _is_valid_email_str('user@example.com') is True
    assert _is_valid_email_str('philip.odame@gmail.com') is True
    assert _is_valid_email_str('{{user.primary_email_address.email_address}}') is False
    assert _is_valid_email_str('{{user.email}}') is False
    assert _is_valid_email_str('not-an-email') is False
    assert _is_valid_email_str('') is False
    assert _is_valid_email_str(None) is False

def test_profile_from_claims_rejects_template_syntax():
    payload = {
        'sub': 'user_123',
        'email': '{{user.primary_email_address.email_address}}',
        'first_name': 'Philip',
        'last_name': 'Odame',
    }
    profile = _profile_from_claims(payload)
    assert profile.email == ''
    assert profile.first_name == 'Philip'
    assert profile.last_name == 'Odame'

def test_primary_email_rejects_template_syntax():
    data = {
        'id': 'user_123',
        'primary_email_address_id': 'idn_1',
        'email_addresses': [
            {'id': 'idn_1', 'email_address': '{{user.primary_email_address.email_address}}', 'verification': {'status': 'verified'}}
        ]
    }
    email, verified = _primary_email(data)
    assert email == ''
    assert verified is False

@pytest.mark.django_db
def test_profile_serializer_sanitizes_template_email():
    user = User(
        email='{{user.primary_email_address.email_address}}',
        first_name='Philip',
        last_name='Odame',
        role=User.Role.CUSTOMER,
    )
    user.save()
    serializer = ProfileSerializer(instance=user)
    data = serializer.data
    assert data['email'] == ''
    assert data['fullName'] == 'Philip Odame'
