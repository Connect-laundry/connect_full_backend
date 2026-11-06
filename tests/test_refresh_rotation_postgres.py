"""
Concurrent refresh-token rotation on PostgreSQL (row locks are no-ops on SQLite).

Run with a Postgres settings module, e.g.:
    pytest --ds=<postgres settings> tests/test_refresh_rotation_postgres.py
"""

import threading
from unittest.mock import patch

import pytest
from django.db import connection
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from users.models import DeviceSession, SessionRefreshToken, User

pytestmark = [
    pytest.mark.skipif(connection.vendor != 'postgresql', reason='needs PostgreSQL row locking'),
    pytest.mark.django_db(transaction=True),
]

N = 8


def _headers(device_id='device-pg', remote_addr='127.0.0.1'):
    return {
        'HTTP_X_DEVICE_ID': device_id,
        'HTTP_X_CLIENT_PLATFORM': 'ios',
        'HTTP_X_CLIENT_VERSION': '1.0.0',
        'HTTP_USER_AGENT': 'SimamePgTest/1.0',
        'REMOTE_ADDR': remote_addr,
    }


@pytest.fixture(autouse=True)
def _no_throttle():
    rates = dict(SimpleRateThrottle.THROTTLE_RATES)
    for key in list(rates):
        rates[key] = '100000/m'
    with patch.object(SimpleRateThrottle, 'THROTTLE_RATES', rates):
        yield


def _login():
    User.objects.create_user(
        email='pg-refresh@example.com', phone='233555777001', password='StrongPass123!',
    )
    response = APIClient().post(
        reverse('auth_login'),
        {'email': 'pg-refresh@example.com', 'password': 'StrongPass123!'},
        format='json',
        **_headers(),
    )
    assert response.status_code == 200, response.data
    return response.data.get('data', response.data)['refreshToken']


def _concurrently(n, fn):
    barrier = threading.Barrier(n)
    results = [None] * n

    def worker(i):
        try:
            barrier.wait(timeout=30)
            results[i] = fn(i)
        except Exception as exc:
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_concurrent_duplicate_refreshes_never_revoke_and_leave_one_live_token():
    refresh = _login()

    def refresh_once(_):
        return APIClient().post(
            reverse('token_refresh'), {'refresh': refresh}, format='json', **_headers(),
        ).status_code

    statuses = _concurrently(N, refresh_once)

    assert all(s == 200 for s in statuses), statuses
    session = DeviceSession.objects.get(device_id='device-pg')
    assert session.revoked_at is None
    live = SessionRefreshToken.objects.filter(session=session, revoked_at__isnull=True)
    assert live.count() == 1
    assert session.current_refresh_jti == live.get().jti


def test_concurrent_replay_from_other_devices_revokes_the_session():
    refresh = _login()
    # The legitimate client rotates first.
    first = APIClient().post(reverse('token_refresh'), {'refresh': refresh}, format='json', **_headers())
    assert first.status_code == 200

    def replay(i):
        return APIClient().post(
            reverse('token_refresh'), {'refresh': refresh}, format='json',
            **_headers(device_id=f'attacker-{i}', remote_addr=f'203.0.113.{i + 1}'),
        ).status_code

    statuses = _concurrently(N, replay)

    assert all(s == 401 for s in statuses), statuses
    session = DeviceSession.objects.get(device_id='device-pg')
    assert session.revoked_reason == 'refresh_token_reuse'
    assert not SessionRefreshToken.objects.filter(session=session, revoked_at__isnull=True).exists()
