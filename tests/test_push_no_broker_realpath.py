"""Reproduce production push conditions without mocks of the dispatch path:
no Celery broker, eager mode off, the real background thread and the real
send_real_push task body. Only the Expo HTTP call is faked (as the network)."""
import threading
import time
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from config.celery import app as celery_app
from marketplace.models import Notification, PushDevice
from marketplace.services.notification_service import NotificationService


class _ExpoResponse:
    status_code = 200
    text = ''

    def __init__(self, tokens):
        self._tokens = tokens

    def json(self):
        return {'data': [{'status': 'ok', 'id': f'ticket-{i}'} for i, _ in enumerate(self._tokens)]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('use_celery', [False, True])
def test_production_like_push_without_broker_is_sent(settings, use_celery):
    settings.PUSH_USE_CELERY = use_celery
    settings.EXPO_PUSH_ENABLED = True
    settings.PUSH_ENVIRONMENT = 'production'
    settings.PUSH_DISPATCH_IN_THREAD = True
    settings.CELERY_TASK_ALWAYS_EAGER = False
    celery_app.conf.update(task_always_eager=False, broker_url='redis://127.0.0.1:6399/1')

    user = get_user_model().objects.create_user(
        email='push-real@example.com', password='x-Pass-12345', phone='233200009999',
    )
    PushDevice.objects.create(
        user=user, token='ExponentPushToken[realpath-1]', environment='production',
        device_id='dev-1', platform='ios', is_active=True,
    )
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append(json)
        return _ExpoResponse([m['to'] for m in json])

    try:
        before = set(threading.enumerate())
        with patch('marketplace.tasks.requests.post', side_effect=fake_post):
            n = NotificationService.notify_user(user, 'Order Placed', 'QA', category='ORDER_CREATED')
            # Wait for the dispatch thread instead of polling the row: SQLite's
            # shared-cache test DB raises "table is locked" (no busy wait) when
            # this thread reads while the dispatch thread writes.
            for t in set(threading.enumerate()) - before:
                t.join(timeout=120)
        n.refresh_from_db()
        assert len(calls) == 1, f'expected exactly one Expo send, got {len(calls)}'
        assert n.push_status == Notification.PushStatus.SENT, n.push_status
    finally:
        celery_app.conf.update(task_always_eager=True)


class _ExpoError:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = '{"errors":[{"code":"AUTHENTICATION_ERROR","message":"The bearer token is invalid."}]}'

    def raise_for_status(self):
        import requests
        raise requests.HTTPError(f'{self.status_code}')


def _prod_like_user(settings, email):
    settings.EXPO_PUSH_ENABLED = True
    settings.PUSH_ENVIRONMENT = 'production'
    user = get_user_model().objects.create_user(email=email, password='x-Pass-12345', phone=f'2332{abs(hash(email)) % 10**8:08d}')
    PushDevice.objects.create(
        user=user, token=f'ExponentPushToken[{email}]', environment='production',
        device_id=f'dev-{email}', platform='ios', is_active=True,
    )
    return user


@pytest.mark.django_db
def test_invalid_expo_access_token_marks_failed_and_alerts_once(settings):
    """Production 2026-09-22: a rejected access token left every push PENDING
    forever (no broker, no sweep). It must fail fast, visibly, without retries."""
    from marketplace.tasks import dispatch_claimed_push, claim_push
    from kombu.exceptions import OperationalError

    user = _prod_like_user(settings, 'expo-401@example.com')
    with patch('marketplace.services.notification_service.NotificationService._queue_push'):
        n = NotificationService.notify_user(user, 'Order Placed', 'QA', category='ORDER_CREATED')
    with patch('marketplace.tasks.send_real_push.delay', side_effect=OperationalError('no broker')), \
         patch('marketplace.tasks.requests.post', return_value=_ExpoError(401)) as post, \
         patch('marketplace.tasks.time.sleep'):
        assert claim_push(n.id)
        dispatch_claimed_push(n.id)
    n.refresh_from_db()
    assert post.call_count == 1, 'a credential rejection must not be retried'
    assert n.push_status == Notification.PushStatus.FAILED
    alerts = Notification.objects.filter(audience=Notification.Audience.ADMIN, category='PUSH_CREDENTIALS')
    assert alerts.count() == 1
    assert 'EXPO_ACCESS_TOKEN' in alerts.first().body


@pytest.mark.django_db
def test_transient_expo_outage_is_retried_and_left_pending(settings):
    from marketplace.tasks import send_push_directly

    user = _prod_like_user(settings, 'expo-503@example.com')
    with patch('marketplace.services.notification_service.NotificationService._queue_push'):
        n = NotificationService.notify_user(user, 'Order Placed', 'QA', category='ORDER_CREATED')
    with patch('marketplace.tasks.requests.post', return_value=_ExpoError(503)) as post:
        send_push_directly(n.id, sleep=lambda _s: None)
    n.refresh_from_db()
    assert post.call_count == 3
    assert n.push_status == Notification.PushStatus.PENDING
