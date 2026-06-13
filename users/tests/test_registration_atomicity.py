"""Regression tests: registration must be all-or-nothing.

If any step after the User row is created fails, the whole request must roll
back so no orphaned User / DeviceSession / SessionRefreshToken survives and the
email/phone stay available for a retry.

The project's custom exception handler turns unhandled errors into a 500 JSON
envelope (it does not re-raise), so these tests assert on the 500 response plus
the absence of any persisted rows.
"""
import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from users.models import User, DeviceSession, SessionRefreshToken
from users.services import auth_service, session_service


def _payload(email='atomic@example.com', phone='233500009001'):
    return {
        'email': email,
        'phone': phone,
        'first_name': 'Atom',
        'last_name': 'Ic',
        'password': 'StrongPass123!',
        'password_confirm': 'StrongPass123!',
        'role': 'OWNER',
    }


def _assert_no_orphans():
    assert User.objects.count() == 0
    assert DeviceSession.objects.count() == 0
    assert SessionRefreshToken.objects.count() == 0


@pytest.mark.django_db(transaction=True)
class TestRegistrationAtomicity:
    def test_happy_path_returns_tokens(self):
        resp = APIClient().post(reverse('auth_register'), _payload(), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data['accessToken']
        assert resp.data['refreshToken']
        assert User.objects.count() == 1
        assert DeviceSession.objects.count() == 1
        assert SessionRefreshToken.objects.count() == 1

    def test_failure_in_token_issuance_rolls_back_user(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError('token issuance exploded')

        # AuthService binds the name via ``from .session_service import ...``,
        # so patch the reference it actually calls.
        monkeypatch.setattr(auth_service, 'issue_tokens_for_user', boom)
        resp = APIClient().post(reverse('auth_register'), _payload(), format='json')
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        _assert_no_orphans()

    def test_failure_after_device_session_rolls_back_everything(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError('refresh record write failed')

        # Fails AFTER DeviceSession.objects.create() but during token issuance.
        monkeypatch.setattr(session_service, '_create_refresh_record', boom)
        resp = APIClient().post(reverse('auth_register'), _payload(), format='json')
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        _assert_no_orphans()

    def test_failure_after_refresh_token_rolls_back_everything(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError('session touch failed')

        # _touch_session runs AFTER the SessionRefreshToken row is created.
        monkeypatch.setattr(session_service, '_touch_session', boom)
        resp = APIClient().post(reverse('auth_register'), _payload(), format='json')
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        _assert_no_orphans()

    def test_email_reusable_after_failed_attempt(self, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr(auth_service, 'issue_tokens_for_user', boom)
        resp = APIClient().post(reverse('auth_register'), _payload(), format='json')
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

        # The same email/phone must succeed on retry once the fault clears.
        monkeypatch.undo()
        resp = APIClient().post(reverse('auth_register'), _payload(), format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert User.objects.filter(email='atomic@example.com').count() == 1
