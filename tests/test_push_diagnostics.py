"""Push visibility: receipts in direct mode, self-service diagnostics, test push.

Production 2026-09-22: the owner's phone received no push notifications
while in-app ones appeared. Direct mode never read Expo receipts, so an
APNs/FCM rejection was indistinguishable from a delivery.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from marketplace.models import Notification, PushDelivery, PushDevice
from marketplace import push_sweep


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(email='diag@example.com', phone='233200000970', password='x-Pass-12345')


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _device(user, env='staging', active=True, token='ExponentPushToken[diag-1]'):
    return PushDevice.objects.create(user=user, token=token, environment=env, device_id='d1', platform='ios', is_active=active)


def _sent_notification(user, device, ticket='tkt-1', age=timedelta(minutes=5)):
    n = Notification.objects.create(user=user, audience=Notification.Audience.USER, title='Order Placed', body='b',
                                    push_status=Notification.PushStatus.SENT)
    d = PushDelivery.objects.create(notification=n, device=device, ticket_id=ticket, status=PushDelivery.Status.TICKET_OK)
    PushDelivery.objects.filter(pk=d.pk).update(created_at=timezone.now() - age)
    return n


@pytest.mark.django_db
def test_receipts_are_checked_without_celery_and_mark_delivery(settings, user):
    settings.EXPO_PUSH_ENABLED = True
    n = _sent_notification(user, _device(user))
    with patch('marketplace.tasks.fetch_push_receipts', return_value={'tkt-1': {'status': 'ok'}}):
        push_sweep.check_pending_receipts()
    n.refresh_from_db()
    assert n.push_status == Notification.PushStatus.DELIVERED


@pytest.mark.django_db
def test_apns_rejection_becomes_failed_and_deactivates_the_token(settings, user):
    settings.EXPO_PUSH_ENABLED = True
    device = _device(user)
    n = _sent_notification(user, device)
    receipt = {'status': 'error', 'message': 'not registered', 'details': {'error': 'DeviceNotRegistered'}}
    with patch('marketplace.tasks.fetch_push_receipts', return_value={'tkt-1': receipt}):
        push_sweep.check_pending_receipts()
    n.refresh_from_db(); device.refresh_from_db()
    assert n.push_status == Notification.PushStatus.FAILED
    assert device.is_active is False
    assert PushDelivery.objects.get(ticket_id='tkt-1').error_code == 'DeviceNotRegistered'


@pytest.mark.django_db
def test_fresh_tickets_wait_for_the_receipt_delay(settings, user):
    settings.EXPO_PUSH_ENABLED = True
    _sent_notification(user, _device(user), age=timedelta(seconds=5))
    with patch('marketplace.tasks.fetch_push_receipts') as fetch:
        push_sweep.check_pending_receipts()
    fetch.assert_not_called()


@pytest.mark.django_db
def test_diagnostics_show_devices_and_outcomes_without_tokens(settings, client, user):
    settings.PUSH_ENVIRONMENT = 'staging'
    device = _device(user)
    _device(user, env='production', token='ExponentPushToken[other-env]')
    n = _sent_notification(user, device)
    PushDelivery.objects.filter(notification=n).update(status=PushDelivery.Status.RECEIPT_ERROR, error_code='InvalidCredentials')
    body = client.get('/api/v1/support/notifications/push-diagnostics/').json()['data']
    assert body['server_environment'] == 'staging'
    assert sorted(d['matches_server'] for d in body['devices']) == [False, True]
    assert body['recent_pushes'][0]['deliveries'][0]['error_code'] == 'InvalidCredentials'
    assert 'ExponentPushToken' not in str(body)


@pytest.mark.django_db
def test_test_push_needs_a_registered_device(settings, client, user):
    settings.PUSH_ENVIRONMENT = 'staging'
    response = client.post('/api/v1/support/notifications/test-push/')
    assert response.status_code == 409
    assert 'not registered' in response.json()['message']


@pytest.mark.django_db
def test_test_push_sends_one_urgent_notification_to_own_devices(settings, client, user):
    settings.PUSH_ENVIRONMENT = 'staging'
    settings.EXPO_PUSH_ENABLED = True
    _device(user)
    with patch('marketplace.services.notification_service.NotificationService._queue_push') as queue:
        response = client.post('/api/v1/support/notifications/test-push/')
    assert response.status_code == 200
    assert response.json()['data']['active_devices'] == 1
    queue.assert_called_once()
    n = Notification.objects.get(user=user, title='Notifications are working')
    assert n.priority == Notification.Priority.URGENT  # quiet hours never hide the test
