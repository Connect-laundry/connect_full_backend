"""In-process recovery of the push outbox in direct mode (no Celery beat).

Production 2026-09-22: two notifications from before the direct-delivery fix
stayed PENDING forever because nothing swept the outbox.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from marketplace import push_sweep
from marketplace.models import Notification


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(email='sweep@example.com', phone='233200000950', password='x-Pass-12345')


def _pending(user, *, age, queued_age=None):
    n = Notification.objects.create(
        user=user, audience=Notification.Audience.USER, title='Order Placed', body='b',
        push_status=Notification.PushStatus.PENDING,
        push_last_queued_at=None if queued_age is None else timezone.now() - queued_age,
    )
    Notification.objects.filter(pk=n.pk).update(created_at=timezone.now() - age)
    return n


@pytest.mark.django_db
def test_rows_older_than_max_age_are_failed_not_sent(settings, user):
    settings.EXPO_PUSH_ENABLED = True
    old = _pending(user, age=timedelta(hours=7), queued_age=timedelta(hours=7))
    with patch('marketplace.tasks.dispatch_claimed_push') as dispatch:
        result = push_sweep.sweep_pending_pushes()
    old.refresh_from_db()
    assert old.push_status == Notification.PushStatus.FAILED
    assert result['expired'] == 1
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_recent_stale_row_is_dispatched_exactly_once(settings, user):
    settings.EXPO_PUSH_ENABLED = True
    stuck = _pending(user, age=timedelta(minutes=20), queued_age=timedelta(minutes=20))
    with patch('marketplace.tasks.dispatch_claimed_push') as dispatch:
        first = push_sweep.sweep_pending_pushes()
        second = push_sweep.sweep_pending_pushes()  # the claim now belongs to the first sweep
    assert first['dispatched'] == 1 and second['dispatched'] == 0
    dispatch.assert_called_once_with(stuck.id)


@pytest.mark.django_db
def test_row_owned_by_a_live_sender_is_left_alone(settings, user):
    settings.EXPO_PUSH_ENABLED = True
    _pending(user, age=timedelta(minutes=20), queued_age=timedelta(minutes=1))  # claim still valid
    _pending(user, age=timedelta(seconds=30))  # brand new: post-commit sender is on it
    with patch('marketplace.tasks.dispatch_claimed_push') as dispatch:
        assert push_sweep.sweep_pending_pushes()['dispatched'] == 0
    dispatch.assert_not_called()


def test_receiver_runs_at_most_once_per_interval_and_defers_to_celery(settings, monkeypatch):
    settings.PUSH_INPROCESS_SWEEP_ENABLED = True
    settings.PUSH_INPROCESS_SWEEP_SECONDS = 120
    settings.PUSH_USE_CELERY = False
    monkeypatch.setattr(push_sweep, '_next_run_at', 0.0)
    started = []
    monkeypatch.setattr(push_sweep.threading, 'Thread', lambda **kw: type('T', (), {'start': lambda self: started.append(kw['name'])})())
    push_sweep.maybe_sweep_after_request()
    push_sweep.maybe_sweep_after_request()
    assert started == ['push-recovery-sweep']
    settings.PUSH_USE_CELERY = True
    monkeypatch.setattr(push_sweep, '_next_run_at', 0.0)
    push_sweep.maybe_sweep_after_request()
    assert started == ['push-recovery-sweep'], 'Celery beat owns recovery in async mode'
