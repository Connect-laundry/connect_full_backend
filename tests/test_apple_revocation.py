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
@patch('users.views.profile.revoke_apple_for_account_deletion')
def test_account_deletion_revokes_before_deleting_the_clerk_user(revoke, delete_clerk, settings):
    from rest_framework.test import APIClient
    from users.models import User

    calls = []
    revoke.side_effect = lambda *_: calls.append('revoke') or 'no_code'
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
    revoke.assert_called_once_with('user_apple', None)


# --- Native sign-in: fresh authorization code at deletion time -------------

from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from users.services.apple_revocation import (  # noqa: E402
    APPLE_TOKEN_URL,
    revoke_apple_for_account_deletion,
    revoke_apple_with_authorization_code,
)

NATIVE_CLIENT_ID = 'com.connectlaundry.app'


def _apple_id_token(rsa_key, sub, aud=NATIVE_CLIENT_ID):
    return jwt.encode({'iss': 'https://appleid.apple.com', 'aud': aud, 'sub': sub,
                       'iat': 1, 'exp': 32503680000}, rsa_key, algorithm='RS256')


def _clerk_user(*subs):
    accounts = [{'provider': 'oauth_apple', 'provider_user_id': s} for s in subs]
    return Mock(status_code=200, json=Mock(return_value={'external_accounts': accounts}),
                raise_for_status=Mock())


@pytest.fixture
def apple_rsa(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class _Jwks:
        def __init__(self, url):
            assert url == 'https://appleid.apple.com/auth/keys'

        def get_signing_key_from_jwt(self, token):
            return Mock(key=key.public_key())

    monkeypatch.setattr('users.services.apple_revocation.jwt.PyJWKClient', _Jwks)
    return key


def _post_router(token_response, revoke_status=200, calls=None):
    def post(url, data=None, timeout=None):
        if calls is not None:
            calls.append((url, dict(data or {})))
        if url == APPLE_TOKEN_URL:
            return token_response
        return Mock(status_code=revoke_status)
    return post


@patch('users.services.apple_revocation.requests.post')
@patch('users.services.apple_revocation.requests.get')
def test_native_code_is_exchanged_with_bundle_id_and_refresh_token_revoked(get, post, apple_settings, apple_rsa):
    get.return_value = _clerk_user('001234.abc.0001')
    calls = []
    token_response = Mock(status_code=200, json=Mock(return_value={
        'refresh_token': 'r-token', 'access_token': 'a-token',
        'id_token': _apple_id_token(apple_rsa, '001234.abc.0001')}))
    post.side_effect = _post_router(token_response, calls=calls)

    assert revoke_apple_with_authorization_code('user_abc', 'c-fresh-code') == 'revoked'

    exchange_url, exchange = calls[0]
    assert exchange_url == APPLE_TOKEN_URL
    assert exchange['client_id'] == NATIVE_CLIENT_ID
    assert exchange['grant_type'] == 'authorization_code' and exchange['code'] == 'c-fresh-code'
    claims = jwt.decode(exchange['client_secret'], apple_settings.public_key(), algorithms=['ES256'],
                        audience='https://appleid.apple.com')
    assert claims['sub'] == NATIVE_CLIENT_ID and claims['iss'] == 'TEAM123456'
    revoke_url, revoke = calls[1]
    assert revoke_url == APPLE_REVOKE_URL
    assert revoke['token'] == 'r-token' and revoke['token_type_hint'] == 'refresh_token'
    assert get.call_args.args[0].endswith('/v1/users/user_abc')


@patch('users.services.apple_revocation.requests.post')
@patch('users.services.apple_revocation.requests.get')
def test_code_for_a_different_apple_account_is_not_revoked(get, post, apple_settings, apple_rsa):
    get.return_value = _clerk_user('001234.owner.0001')
    calls = []
    token_response = Mock(status_code=200, json=Mock(return_value={
        'refresh_token': 'r-token', 'id_token': _apple_id_token(apple_rsa, '009999.someone.else')}))
    post.side_effect = _post_router(token_response, calls=calls)

    assert revoke_apple_with_authorization_code('user_abc', 'c-code') == 'subject_mismatch'
    assert [url for url, _ in calls] == [APPLE_TOKEN_URL]


@patch('users.services.apple_revocation.requests.post')
@patch('users.services.apple_revocation.requests.get')
def test_id_token_for_another_client_is_rejected(get, post, apple_settings, apple_rsa):
    get.return_value = _clerk_user('001234.abc.0001')
    token_response = Mock(status_code=200, json=Mock(return_value={
        'refresh_token': 'r', 'id_token': _apple_id_token(apple_rsa, '001234.abc.0001', aud='other.app')}))
    post.side_effect = _post_router(token_response)

    assert revoke_apple_with_authorization_code('user_abc', 'c-code') == 'error'


@patch('users.services.apple_revocation.requests.post')
@patch('users.services.apple_revocation.requests.get')
def test_failed_exchange_falls_back_to_clerk_tokens_and_reports_outcome(get, post, apple_settings):
    get.side_effect = [_clerk_user('001234.abc.0001'), Mock(status_code=404), Mock(status_code=404)]
    post.return_value = Mock(status_code=400)

    assert revoke_apple_for_account_deletion('user_abc', 'expired-code') == 'exchange_failed'


@patch('users.services.apple_revocation.requests.post')
@patch('users.services.apple_revocation.requests.get')
def test_no_code_uses_the_clerk_token_path(get, post, apple_settings):
    get.return_value = _clerk_tokens('legacy-oauth-token')
    post.return_value = Mock(status_code=200)

    assert revoke_apple_for_account_deletion('user_abc', None) == 'revoked_via_clerk_token'


def test_code_path_reports_missing_configuration(settings):
    settings.CLERK_SECRET_KEY = 'sk_test_clerk'
    settings.APPLE_TEAM_ID = ''
    assert revoke_apple_with_authorization_code('user_abc', 'c-code') == 'not_configured'


def test_oversized_code_is_ignored(apple_settings):
    assert revoke_apple_with_authorization_code('user_abc', 'x' * 2000) == 'no_code'


@pytest.mark.django_db
@patch('users.views.profile.delete_clerk_user')
@patch('users.views.profile.revoke_apple_for_account_deletion')
def test_account_deletion_passes_the_apple_code_and_still_deletes(revoke, delete_clerk):
    from rest_framework.test import APIClient
    from users.models import User

    revoke.return_value = 'revoked'
    user = User.objects.create_user(
        email='apple-native@example.com', phone='233555970002', password='StrongPass123!')
    user.clerk_user_id = 'user_native'
    user.save(update_fields=['clerk_user_id'])
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.delete('/api/v1/auth/account/',
                             {'reason': 'other', 'apple_authorization_code': 'c-fresh'}, format='json')

    assert response.status_code == 200, response.content[:300]
    revoke.assert_called_once_with('user_native', 'c-fresh')
    delete_clerk.assert_called_once_with('user_native')
    user.refresh_from_db()
    assert user.is_active is False and user.clerk_user_id is None


# --- Configuration check and response status ------------------------------

from users.services.apple_revocation import check_native_revocation_config  # noqa: E402


@patch('users.services.apple_revocation.requests.post')
def test_config_check_ok_only_when_apple_issues_a_client_token(post, apple_settings):
    post.return_value = Mock(status_code=200, json=Mock(return_value={'access_token': 'x', 'token_type': 'Bearer'}))
    assert check_native_revocation_config() == 'ok'
    sent = post.call_args.kwargs['data']
    assert sent['client_id'] == NATIVE_CLIENT_ID
    assert sent['grant_type'] == 'client_credentials' and sent['scope'] == 'user.migration'


@patch('users.services.apple_revocation.requests.post')
def test_config_check_does_not_trust_invalid_grant(post, apple_settings):
    # Apple answers invalid_grant before checking the client, so it proves nothing.
    post.return_value = Mock(status_code=400, json=Mock(return_value={'error': 'invalid_grant'}))
    assert check_native_revocation_config() == 'apple_rejected:400:invalid_grant'


@patch('users.services.apple_revocation.requests.post')
def test_config_check_reports_invalid_client(post, apple_settings):
    post.return_value = Mock(status_code=400, json=Mock(return_value={'error': 'invalid_client'}))
    assert check_native_revocation_config() == 'apple_rejected:400:invalid_client'


def test_config_check_reports_missing_configuration(settings):
    settings.APPLE_TEAM_ID = ''
    assert check_native_revocation_config() == 'not_configured'


def test_config_check_command_fails_loudly_when_not_ready(settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    settings.APPLE_TEAM_ID = ''
    with pytest.raises(CommandError):
        call_command('check_apple_revocation_config')


@pytest.mark.django_db
@pytest.mark.parametrize('outcome,expected', [
    ('revoked', 'revoked'), ('revoked_via_clerk_token', 'not_revoked'),
    ('exchange_failed', 'not_revoked'), ('no_code', 'not_revoked'),
])
@patch('users.views.profile.delete_clerk_user')
@patch('users.views.profile.revoke_apple_for_account_deletion')
def test_deletion_response_reports_apple_revocation_status(revoke, delete_clerk, outcome, expected):
    from rest_framework.test import APIClient
    from users.models import User

    revoke.return_value = outcome
    user = User.objects.create_user(
        email=f'status-{outcome}@example.com', phone=f'23355597{abs(hash(outcome)) % 10000:04d}',
        password='StrongPass123!')
    user.clerk_user_id = f'user_{outcome}'
    user.save(update_fields=['clerk_user_id'])
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.delete('/api/v1/auth/account/', {'reason': 'other'}, format='json')

    assert response.status_code == 200
    assert response.json()['data']['apple_revocation'] == expected


@pytest.mark.django_db
def test_password_only_account_reports_not_applicable():
    from rest_framework.test import APIClient
    from users.models import User

    user = User.objects.create_user(email='pw-only@example.com', phone='233555970099', password='StrongPass123!')
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.delete('/api/v1/auth/account/', {'reason': 'other'}, format='json')
    assert response.status_code == 200
    assert response.json()['data']['apple_revocation'] == 'not_applicable'
