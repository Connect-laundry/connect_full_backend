from dataclasses import replace
import base64
import hashlib
import hmac
import json
import time

import pytest
from django.contrib import admin
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.test import APIClient

from users.admin import UserAdmin
from users.checks import clerk_production_configuration_check
from users.models import ClerkWebhookEvent, DeviceSession, User
from users.services.clerk_service import ClerkProfile, fetch_clerk_profile, sync_user_from_clerk


def _profile(
    clerk_user_id='user_google_123',
    email='social@example.com',
    first_name='Social',
    last_name='User',
    provider='oauth_google',
):
    return ClerkProfile(
        clerk_user_id=clerk_user_id,
        email=email,
        first_name=first_name,
        last_name=last_name,
        image_url='https://img.clerk.com/avatar.png',
        provider=provider,
    )


def _signed_webhook_headers(body: bytes, *, svix_id='msg_123', secret_key=b'test-secret', timestamp=None):
    timestamp = timestamp or int(time.time())
    secret = 'whsec_' + base64.b64encode(secret_key).decode('utf-8')
    signed_content = f'{svix_id}.{timestamp}.'.encode('utf-8') + body
    signature = base64.b64encode(hmac.new(secret_key, signed_content, hashlib.sha256).digest()).decode('utf-8')
    return secret, {
        'HTTP_SVIX_ID': svix_id,
        'HTTP_SVIX_TIMESTAMP': str(timestamp),
        'HTTP_SVIX_SIGNATURE': f'v1,{signature}',
    }


def _clerk_user_payload(event_type='user.created', clerk_user_id='user_google_123', email='social@example.com'):
    return {
        'type': event_type,
        'data': {
            'id': clerk_user_id,
            'first_name': 'Social',
            'last_name': 'User',
            'image_url': 'https://img.clerk.com/avatar.png',
            'primary_email_address_id': 'email_1',
            'email_addresses': [
                {
                    'id': 'email_1',
                    'email_address': email,
                    'verification': {'status': 'verified'},
                }
            ],
            'phone_numbers': [
                {
                    'id': 'phone_1',
                    'verification': {'status': 'verified'},
                }
            ],
            'external_accounts': [{'provider': 'oauth_google', 'id': 'ext_1'}],
            'created_at': 1710000000000,
            'updated_at': 1710000100000,
            'last_sign_in_at': 1710000200000,
            'public_metadata': {'tier': 'standard'},
            'unsafe_metadata': {},
        },
    }


def _post_clerk_webhook(client, payload, *, svix_id='msg_123', timestamp=None):
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    secret, headers = _signed_webhook_headers(body, svix_id=svix_id, timestamp=timestamp)
    return secret, client.generic(
        'POST',
        reverse('auth_clerk_webhook'),
        data=body,
        content_type='application/json',
        **headers,
    )


@pytest.mark.django_db
class TestClerkSocialAuth:
    def test_social_login_creates_local_user_and_internal_session(self, monkeypatch):
        def fake_auth(token, *, requested_role=None, request=None):
            return sync_user_from_clerk(
                profile=_profile(provider='oauth_google'),
                requested_role=requested_role,
                request=request,
            )

        monkeypatch.setattr('users.views.social.authenticate_clerk_token', fake_auth)

        response = APIClient().post(
            reverse('auth_social_login'),
            {'clerk_token': 'valid-clerk-token', 'role': 'CUSTOMER'},
            format='json',
            HTTP_X_DEVICE_ID='device-social-1',
        )

        assert response.status_code == status.HTTP_200_OK
        payload = response.data.get('data', response.data)
        assert payload['accessToken']
        assert payload['refreshToken']
        assert payload['user']['role'] == User.Role.CUSTOMER

        user = User.objects.get(email='social@example.com')
        assert user.clerk_user_id == 'user_google_123'
        assert user.social_provider == 'oauth_google'
        assert user.social_profile_image_url == 'https://img.clerk.com/avatar.png'
        assert user.has_usable_password() is False
        assert DeviceSession.objects.filter(user=user, device_id='device-social-1').exists()

    def test_invalid_clerk_token_is_rejected(self, monkeypatch):
        def fake_auth(*args, **kwargs):
            raise AuthenticationFailed('Invalid Clerk session token.')

        monkeypatch.setattr('users.views.social.authenticate_clerk_token', fake_auth)

        response = APIClient().post(
            reverse('auth_social_login'),
            {'clerk_token': 'invalid-token'},
            format='json',
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert not User.objects.exists()

    def test_expired_clerk_token_is_rejected(self, monkeypatch):
        def fake_auth(*args, **kwargs):
            raise AuthenticationFailed('Clerk session token has expired.')

        monkeypatch.setattr('users.views.social.authenticate_clerk_token', fake_auth)

        response = APIClient().post(
            reverse('auth_social_login'),
            {'clerk_token': 'expired-token'},
            format='json',
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'expired' in str(response.data).lower()

    def test_existing_user_is_updated_without_duplicate(self):
        user = User.objects.create_user(
            email='social@example.com',
            phone='233555000099',
            password='StrongPass123!',
            first_name='Old',
            last_name='Name',
            role=User.Role.OWNER,
        )

        synced, created = sync_user_from_clerk(
            profile=_profile(first_name='New', last_name='Name', provider='oauth_facebook'),
            requested_role=User.Role.CUSTOMER,
        )

        assert created is False
        assert synced.id == user.id
        assert User.objects.count() == 1
        synced.refresh_from_db()
        assert synced.first_name == 'New'
        assert synced.role == User.Role.OWNER
        assert synced.clerk_user_id == 'user_google_123'
        assert synced.social_provider == 'oauth_facebook'

    def test_social_sync_requires_verified_email(self):
        with pytest.raises(ValidationError):
            sync_user_from_clerk(
                profile=replace(_profile(email='unverified@example.com'), email_verified=False),
                requested_role=User.Role.CUSTOMER,
            )

        assert not User.objects.exists()

    def test_social_sync_canonicalizes_email_case(self):
        synced, created = sync_user_from_clerk(
            profile=_profile(email='Social@Example.COM'),
            requested_role=User.Role.CUSTOMER,
        )

        assert created is True
        assert synced.email == 'social@example.com'

    def test_duplicate_clerk_login_does_not_create_duplicate_accounts(self):
        synced, created = sync_user_from_clerk(
            profile=_profile(email='social@example.com'),
            requested_role=User.Role.CUSTOMER,
        )
        assert created is True

        second, second_created = sync_user_from_clerk(
            profile=_profile(email='social+updated@example.com', first_name='Updated'),
            requested_role=User.Role.OWNER,
        )

        assert second_created is False
        assert second.id == synced.id
        assert User.objects.count() == 1
        second.refresh_from_db()
        assert second.email == 'social+updated@example.com'
        assert second.role == User.Role.CUSTOMER

    def test_privileged_role_request_is_rejected(self):
        response = APIClient().post(
            reverse('auth_social_login'),
            {'clerk_token': 'valid-token', 'role': 'ADMIN'},
            format='json',
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not User.objects.exists()

    def test_clerk_bearer_token_can_authenticate_default_drf_views(self, monkeypatch):
        def fake_auth(token, *, requested_role=None, request=None):
            return sync_user_from_clerk(
                profile=_profile(email='bearer@example.com'),
                requested_role=User.Role.CUSTOMER,
                request=request,
            )

        monkeypatch.setattr('users.auth.authentication.authenticate_clerk_token', fake_auth)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer clerk-session-token')

        response = client.get(reverse('auth_me'))

        assert response.status_code == status.HTTP_200_OK
        payload = response.data.get('data', response.data)
        assert payload['user']['email'] == 'bearer@example.com'

    def test_clerk_bearer_token_can_authenticate_explicit_legal_views(self, monkeypatch):
        def fake_auth(token, *, requested_role=None, request=None):
            return sync_user_from_clerk(
                profile=_profile(email='legal-bearer@example.com'),
                requested_role=User.Role.CUSTOMER,
                request=request,
            )

        monkeypatch.setattr('users.auth.authentication.authenticate_clerk_token', fake_auth)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer clerk-session-token')

        response = client.get(reverse('legal_current_versions'))

        assert response.status_code == status.HTTP_200_OK

    def test_clerk_profile_fetch_uses_configured_timeout_and_verified_primary_email(self, settings, monkeypatch):
        settings.CLERK_SECRET_KEY = 'test-secret'
        settings.CLERK_API_BASE_URL = 'https://api.clerk.test'
        settings.CLERK_API_TIMEOUT_SECONDS = 9
        calls = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    'id': 'user_google_123',
                    'primary_email_address_id': 'email_1',
                    'email_addresses': [
                        {
                            'id': 'email_1',
                            'email_address': 'verified@example.com',
                            'verification': {'status': 'verified'},
                        }
                    ],
                    'external_accounts': [{'provider': 'oauth_google'}],
                }

        def fake_get(url, *, headers, timeout):
            calls['url'] = url
            calls['headers'] = headers
            calls['timeout'] = timeout
            return FakeResponse()

        monkeypatch.setattr('users.services.clerk_service.requests.get', fake_get)

        profile = fetch_clerk_profile({'sub': 'user_google_123'})

        assert calls['url'] == 'https://api.clerk.test/v1/users/user_google_123'
        assert calls['headers']['Authorization'] == 'Bearer test-secret'
        assert calls['timeout'] == 9
        assert profile.email == 'verified@example.com'
        assert profile.email_verified is True

    def test_signed_clerk_user_created_webhook_syncs_local_user(self, settings):
        payload = _clerk_user_payload()
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        secret, headers = _signed_webhook_headers(body)
        settings.CLERK_WEBHOOK_SECRET = secret
        response = APIClient().generic(
            'POST',
            reverse('auth_clerk_webhook'),
            data=body,
            content_type='application/json',
            **headers,
        )

        assert response.status_code == status.HTTP_200_OK
        user = User.objects.get(clerk_user_id='user_google_123')
        assert user.email == 'social@example.com'
        assert user.auth_provider == 'oauth_google'
        assert user.email_verified is True
        assert user.phone_verified is True
        assert user.clerk_status == 'active'
        assert user.clerk_metadata['public_metadata'] == {'tier': 'standard'}
        assert ClerkWebhookEvent.objects.filter(svix_id='msg_123', status=ClerkWebhookEvent.Status.PROCESSED).exists()

    def test_clerk_webhook_rejects_invalid_signature(self, settings):
        settings.CLERK_WEBHOOK_SECRET = 'whsec_' + base64.b64encode(b'test-secret').decode('utf-8')
        body = json.dumps(_clerk_user_payload(), separators=(',', ':')).encode('utf-8')
        response = APIClient().generic(
            'POST',
            reverse('auth_clerk_webhook'),
            data=body,
            content_type='application/json',
            HTTP_SVIX_ID='msg_bad',
            HTTP_SVIX_TIMESTAMP=str(int(time.time())),
            HTTP_SVIX_SIGNATURE='v1,bad-signature',
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert not User.objects.exists()

    def test_clerk_webhook_rejects_stale_timestamp_replay(self, settings):
        stale_timestamp = int(time.time()) - 1000
        payload = _clerk_user_payload()
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        secret, headers = _signed_webhook_headers(body, timestamp=stale_timestamp)
        settings.CLERK_WEBHOOK_SECRET = secret
        settings.CLERK_WEBHOOK_TOLERANCE_SECONDS = 300

        response = APIClient().generic(
            'POST',
            reverse('auth_clerk_webhook'),
            data=body,
            content_type='application/json',
            **headers,
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert not ClerkWebhookEvent.objects.exists()

    def test_duplicate_clerk_webhook_delivery_is_idempotent(self, settings):
        payload = _clerk_user_payload()
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        secret, headers = _signed_webhook_headers(body, svix_id='msg_duplicate')
        settings.CLERK_WEBHOOK_SECRET = secret
        client = APIClient()

        first = client.generic('POST', reverse('auth_clerk_webhook'), data=body, content_type='application/json', **headers)
        second = client.generic('POST', reverse('auth_clerk_webhook'), data=body, content_type='application/json', **headers)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert second.data['status'] == 'duplicate'
        assert User.objects.count() == 1
        assert ClerkWebhookEvent.objects.filter(svix_id='msg_duplicate').count() == 1

    def test_unverified_user_updated_webhook_updates_existing_user_without_creating_new_one(self, settings):
        user, _ = sync_user_from_clerk(profile=_profile(), requested_role=User.Role.CUSTOMER)
        payload = _clerk_user_payload(event_type='user.updated', clerk_user_id=user.clerk_user_id)
        payload['data']['email_addresses'][0]['verification'] = {'status': 'unverified'}
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        secret, headers = _signed_webhook_headers(body, svix_id='msg_unverified_update')
        settings.CLERK_WEBHOOK_SECRET = secret

        response = APIClient().generic('POST', reverse('auth_clerk_webhook'), data=body, content_type='application/json', **headers)

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert User.objects.count() == 1
        assert user.email_verified is False
        assert user.is_verified is False

    def test_clerk_user_deleted_webhook_soft_deactivates_local_user(self, settings):
        user, _ = sync_user_from_clerk(profile=_profile(), requested_role=User.Role.CUSTOMER)
        payload = {'type': 'user.deleted', 'data': {'id': user.clerk_user_id}}
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        secret, headers = _signed_webhook_headers(body, svix_id='msg_deleted')
        settings.CLERK_WEBHOOK_SECRET = secret

        response = APIClient().generic('POST', reverse('auth_clerk_webhook'), data=body, content_type='application/json', **headers)

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.is_active is False
        assert user.clerk_status == 'deleted'
        assert user.deactivated_at is not None

    def test_user_admin_exposes_clerk_identity_controls(self):
        user_admin = UserAdmin(User, admin.site)

        assert 'short_clerk_id' in user_admin.list_display
        assert 'sync_health' in user_admin.list_display
        assert 'clerk_user_id' in user_admin.search_fields
        assert 'auth_provider' in user_admin.list_filter
        assert 'resync_clerk_users' in user_admin.actions

    def test_production_system_check_requires_clerk_webhook_and_jwt_config(self, settings, monkeypatch):
        settings.DEBUG = False
        for name in (
            'CLERK_APPLICATION_ID',
            'CLERK_PUBLISHABLE_KEY',
            'CLERK_SECRET_KEY',
            'CLERK_JWKS_URL',
            'CLERK_JWT_AUDIENCE',
            'CLERK_WEBHOOK_SECRET',
            'CLERK_JWT_ISSUER',
            'CLERK_ISSUER',
        ):
            monkeypatch.delenv(name, raising=False)

        errors = clerk_production_configuration_check(None)

        error_messages = [error.msg for error in errors]
        assert any('CLERK_JWT_AUDIENCE' in message for message in error_messages)
        assert any('CLERK_WEBHOOK_SECRET' in message for message in error_messages)
        assert any('CLERK_JWT_ISSUER or CLERK_ISSUER' in message for message in error_messages)
