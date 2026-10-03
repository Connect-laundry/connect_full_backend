"""Deleting an account revokes Sign in with Apple (App Store 5.1.1(v))."""
from unittest.mock import Mock, patch

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from users.services.apple_revocation import APPLE_REVOKE_URL, revoke_apple_sign_in


@pytest.fixture
def apple_settings(settings):
    key = ec.generate_private_key(ec.SECP256R1())
    settings.CLERK_SECRET_KEY = 'sk_test_clerk'
    settings.APPLE_TEAM_ID = 'TEAM123456'
    settings.APPLE_KEY_ID = 'KEY1234567'
    settings.APPLE_CLIENT_ID = 'com.connectlaundry.signin'
    # Render env vars often hold the .p8 with literal \n sequences.
    settings.APPLE_PRIVATE_KEY = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode().replace('\n', '\\n')
    return key


def _clerk_tokens(*tokens, status=200):
    return Mock(status_code=status, json=Mock(return_value=[{'token': t} for t in tokens]),
                raise_for_status=Mock())


@patch('users.services.apple_revocation.requests.post')
@patch('users.services.apple_revocation.requests.get')
def test_revokes_each_apple_token_with_a_signed_client_secret(get, post, apple_settings):
    get.return_value = _clerk_tokens('apple-token-1')
    post.return_value = Mock(status_code=200)

    assert revoke_apple_sign_in('user_abc') is True

    assert get.call_args.args[0].endswith('/v1/users/user_abc/oauth_access_tokens/oauth_apple')
    url, form = post.call_args.args[0], post.call_args.kwargs['data']
    assert url == APPLE_REVOKE_URL
    assert form['token'] == 'apple-token-1'
    assert form['client_id'] == 'com.connectlaundry.signin'
    claims = jwt.decode(
        form['client_secret'], apple_settings.public_key(), algorithms=['ES256'],
        audience='https://appleid.apple.com')
    assert claims['iss'] == 'TEAM123456' and claims['sub'] == 'com.connectlaundry.signin'
    assert jwt.get_unverified_header(form['client_secret'])['kid'] == 'KEY1234567'


@patch('users.services.apple_revocation.requests.post')
@patch('users.services.apple_revocation.requests.get')
def test_user_without_apple_sign_in_is_left_alone(get, post, apple_settings):
    get.return_value = _clerk_tokens()
    assert revoke_apple_sign_in('user_abc') is False
    post.assert_not_called()


@patch('users.services.apple_revocation.requests.get')
def test_skips_without_contacting_anyone_when_apple_is_not_configured(get, settings):
    settings.CLERK_SECRET_KEY = 'sk_test_clerk'
    settings.APPLE_PRIVATE_KEY = ''
    assert revoke_apple_sign_in('user_abc') is False
    get.assert_not_called()


@patch('users.services.apple_revocation.requests.get', side_effect=requests.Timeout)
def test_an_outage_never_raises(_get, apple_settings):
    assert revoke_apple_sign_in('user_abc') is False


@pytest.mark.django_db
@patch('users.views.profile.delete_clerk_user')
@patch('users.views.profile.revoke_apple_sign_in')
def test_account_deletion_revokes_before_deleting_the_clerk_user(revoke, delete_clerk, settings):
    from rest_framework.test import APIClient
    from users.models import User

    calls = []
    revoke.side_effect = lambda *_: calls.append('revoke')
    delete_clerk.side_effect = lambda *_: calls.append('delete')
    user = User.objects.create_user(
        email='apple-delete@example.com', phone='233555970001', password='StrongPass123!')
    user.clerk_user_id = 'user_apple'
    user.save(update_fields=['clerk_user_id'])
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.delete('/api/v1/auth/account/')

    assert response.status_code == 200, response.content[:300]
    assert calls == ['revoke', 'delete']
    revoke.assert_called_once_with('user_apple')
