import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from users.models import DeviceSession, PasswordResetToken, SessionRefreshToken, User


def _device_headers(device_id='device-1'):
    return {
        'HTTP_X_DEVICE_ID': device_id,
        'HTTP_X_CLIENT_PLATFORM': 'ios',
        'HTTP_X_CLIENT_VERSION': '1.0.0',
        'HTTP_USER_AGENT': 'ConnectLaundryTests/1.0',
    }


def _create_user():
    return User.objects.create_user(email='customer@example.com',
        phone='233555000001',
        password='StrongPass123!',
        first_name='Test',
        last_name='Customer',
    )


def _login(client: APIClient, email='customer@example.com', password='StrongPass123!', device_id='device-1'):
    response = client.post(
        reverse('auth_login'),
        {'email': email, 'password': password},
        format='json',
        **_device_headers(device_id),
    )
    assert response.status_code == status.HTTP_200_OK
    return response.data.get('data', response.data)


@pytest.mark.django_db
class TestAuthSessions:
    def test_login_creates_tracked_device_session(self, client):
        user = _create_user()

        payload = _login(client)

        session = DeviceSession.objects.get(user=user)
        assert payload['refreshToken']
        assert session.device_id == 'device-1'
        assert session.platform == 'ios'
        assert session.current_refresh_jti
        assert SessionRefreshToken.objects.filter(session=session, jti=session.current_refresh_jti).exists()

    def test_refresh_rotates_token_and_reuse_revokes_family(self, client):
        _create_user()
        login_payload = _login(client)

        refresh_response = client.post(
            reverse('token_refresh'),
            {'refresh': login_payload['refreshToken']},
            format='json',
            **_device_headers(),
        )
        assert refresh_response.status_code == status.HTTP_200_OK
        rotated = refresh_response.data.get('data', refresh_response.data)
        assert rotated['refreshToken'] != login_payload['refreshToken']

        reuse_response = client.post(
            reverse('token_refresh'),
            {'refresh': login_payload['refreshToken']},
            format='json',
            **_device_headers(),
        )
        assert reuse_response.status_code == status.HTTP_401_UNAUTHORIZED

        session = DeviceSession.objects.get(device_id='device-1')
        session.refresh_from_db()
        assert session.revoked_at is not None

        chained_refresh = client.post(
            reverse('token_refresh'),
            {'refresh': rotated['refreshToken']},
            format='json',
            **_device_headers(),
        )
        assert chained_refresh.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_revokes_current_session_chain(self, client):
        _create_user()
        login_payload = _login(client)

        auth_client = APIClient()
        auth_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_payload['accessToken']}")
        logout_response = auth_client.post(
            reverse('auth_logout'),
            {'refresh': login_payload['refreshToken']},
            format='json',
            **_device_headers(),
        )
        assert logout_response.status_code == status.HTTP_200_OK

        refresh_response = client.post(
            reverse('token_refresh'),
            {'refresh': login_payload['refreshToken']},
            format='json',
            **_device_headers(),
        )
        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_password_reset_revokes_existing_sessions(self, client):
        user = _create_user()
        login_payload = _login(client)
        token_record, raw_token = PasswordResetToken.create_for_user(user)

        response = client.post(
            reverse('auth_reset_password'),
            {
                'reset_id': str(token_record.id),
                'token': raw_token,
                'new_password': 'EvenStrongerPass123!',
                'confirm_password': 'EvenStrongerPass123!',
            },
            format='json',
            **_device_headers(),
        )
        assert response.status_code == status.HTTP_200_OK

        refresh_response = client.post(
            reverse('token_refresh'),
            {'refresh': login_payload['refreshToken']},
            format='json',
            **_device_headers(),
        )
        assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_active_sessions_lists_current_session(self, client):
        _create_user()
        login_payload = _login(client, device_id='device-xyz')

        auth_client = APIClient()
        auth_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_payload['accessToken']}")
        response = auth_client.get(reverse('auth_sessions'), **_device_headers('device-xyz'))

        assert response.status_code == status.HTTP_200_OK
        payload = response.data.get('data', response.data)
        sessions = payload['sessions']
        assert len(sessions) == 1
        assert sessions[0]['device_id'] == 'device-xyz'
        assert sessions[0]['current'] is True

    def test_login_is_throttled_per_account(self, client):
        _create_user()

        for _ in range(5):
            response = client.post(
                reverse('auth_login'),
                {'email': 'customer@example.com', 'password': 'WrongPass123!'},
                format='json',
                **_device_headers(),
            )
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        throttled = client.post(
            reverse('auth_login'),
            {'email': 'customer@example.com', 'password': 'WrongPass123!'},
            format='json',
            **_device_headers(),
        )
        assert throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS
