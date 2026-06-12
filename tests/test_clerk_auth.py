import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from users.models import DeviceSession, User
from users.services.clerk_service import ClerkProfile, sync_user_from_clerk


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
