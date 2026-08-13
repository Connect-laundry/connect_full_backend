from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
import requests
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from marketplace.models import PushDevice
from users.models import Address, PasswordResetToken, User


@pytest.mark.django_db
class TestAccountDeletion:
    def _clerk_user(self):
        return User.objects.create_user(
            email='delete-clerk@example.com',
            phone='233555330001',
            password='StrongPass123!',
            first_name='Delete',
            last_name='Clerk',
            clerk_user_id='user_delete_123',
            auth_provider='oauth_google',
            primary_email='delete-clerk@example.com',
            social_provider='oauth_google',
            social_profile_image_url='https://img.clerk.com/private.png',
            clerk_metadata={'unsafe_metadata': {'address': 'private'}},
            referral_code='DELETE123',
            email_verified=True,
            phone_verified=True,
        )

    @override_settings(
        ROOT_URLCONF='config.urls',
        CLERK_SECRET_KEY='sk_test_clerk',
        CLERK_API_BASE_URL='https://api.clerk.test',
        CLERK_API_TIMEOUT_SECONDS=3,
    )
    @patch('users.services.clerk_service.requests.delete')
    def test_clerk_account_deletion_removes_identity_and_delivery_channels(self, delete_request):
        user = self._clerk_user()
        Address.objects.create(user=user, label='Home', address_line1='Private Street')
        PasswordResetToken.objects.create(
            user=user,
            token_hash='a' * 64,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        PushDevice.objects.create(
            user=user,
            token='ExponentPushToken[private-device]',
            device_id='private-device',
            platform=PushDevice.Platform.ANDROID,
        )
        delete_request.return_value = Mock(status_code=204)

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.delete('/api/v1/auth/account/', {'reason': 'privacy_request'}, format='json')

        assert response.status_code == status.HTTP_200_OK
        delete_request.assert_called_once_with(
            'https://api.clerk.test/v1/users/user_delete_123',
            headers={'Authorization': 'Bearer sk_test_clerk'},
            timeout=3,
        )

        user.refresh_from_db()
        assert user.is_active is False
        assert user.is_verified is False
        assert user.email.startswith('deleted-') and user.email.endswith('@deleted.simame')
        assert user.phone.startswith('deleted-')
        assert user.first_name == '' and user.last_name == ''
        assert user.clerk_user_id is None
        assert user.primary_email == ''
        assert user.auth_provider == '' and user.social_provider == ''
        assert user.social_profile_image_url == ''
        assert user.clerk_metadata == {}
        assert user.referral_code is None and user.referred_by is None
        assert user.email_verified is False and user.phone_verified is False
        assert user.clerk_status == 'deleted'
        assert not user.has_usable_password()
        assert not user.addresses.exists()
        assert not user.password_reset_tokens.exists()
        assert not PushDevice.objects.filter(user=user).exists()

    @override_settings(ROOT_URLCONF='config.urls', CLERK_SECRET_KEY='sk_test_clerk')
    @patch('users.services.clerk_service.requests.delete', side_effect=requests.Timeout)
    def test_clerk_api_failure_does_not_claim_local_deletion_succeeded(self, _delete_request):
        user = self._clerk_user()
        PushDevice.objects.create(user=user, token='ExponentPushToken[retry-me]')
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete('/api/v1/auth/account/', format='json')

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        user.refresh_from_db()
        assert user.is_active is True
        assert user.email == 'delete-clerk@example.com'
        assert user.clerk_user_id == 'user_delete_123'
        assert PushDevice.objects.filter(user=user, is_active=True).exists()

    @override_settings(ROOT_URLCONF='config.urls', CLERK_SECRET_KEY='')
    def test_missing_clerk_secret_fails_closed_for_clerk_linked_user(self):
        user = self._clerk_user()
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.delete('/api/v1/auth/account/', format='json')

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        user.refresh_from_db()
        assert user.is_active is True
        assert user.clerk_user_id == 'user_delete_123'
