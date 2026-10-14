"""Unit tests for admin_notifications Celery tasks and kick_dispatch integration."""
from unittest.mock import patch
import pytest
from admin_notifications.tasks import (
    dispatch_notifications_task,
    reconcile_sms_reports_task,
    purge_old_payloads_task,
)
from admin_notifications.services.kick import kick_dispatch
from admin_notifications.tests.helpers import configure, make_world

pytestmark = pytest.mark.django_db


def test_dispatch_notifications_task_runs_dispatch_due(settings):
    configure(settings)
    with patch('admin_notifications.services.dispatcher.dispatch_due') as mock_dispatch:
        from admin_notifications.services.dispatcher import DispatchResult
        mock_dispatch.return_value = DispatchResult(claimed=2, accepted=2)
        res = dispatch_notifications_task.apply(kwargs={'limit': 10}).get()
        assert res['claimed'] == 2
        assert res['accepted'] == 2
        mock_dispatch.assert_called_once_with(limit=10, channel=None)


def test_reconcile_sms_reports_task_executes(settings):
    configure(settings)
    with patch('admin_notifications.services.maintenance.reconcile_sms_reports') as mock_reconcile:
        mock_reconcile.return_value = {'asked': 5, 'updated': 3}
        res = reconcile_sms_reports_task.apply().get()
        assert res == {'asked': 5, 'updated': 3}
        mock_reconcile.assert_called_once()


def test_purge_old_payloads_task_executes(settings):
    configure(settings)
    with patch('admin_notifications.services.maintenance.purge_old_payloads') as mock_purge:
        mock_purge.return_value = {'payloads_purged': 10, 'send_logs_deleted': 2}
        res = purge_old_payloads_task.apply().get()
        assert res == {'payloads_purged': 10, 'send_logs_deleted': 2}
        mock_purge.assert_called_once()


def test_kick_dispatch_uses_celery_when_enabled(settings):
    configure(settings)
    settings.ADMIN_NOTIFICATIONS_USE_CELERY = True
    with patch('utils.tasks.safe_task_delay') as mock_delay:
        mock_delay.return_value = True
        kick_dispatch()
        assert mock_delay.called
        task_arg = mock_delay.call_args[0][0]
        assert task_arg == dispatch_notifications_task


def test_kick_dispatch_falls_back_when_celery_delay_returns_false(settings):
    configure(settings)
    settings.ADMIN_NOTIFICATIONS_USE_CELERY = True
    settings.ADMIN_NOTIFICATION_DISPATCH_IN_THREAD = True
    with patch('utils.tasks.safe_task_delay', return_value=False):
        with patch('admin_notifications.services.kick._get_executor') as mock_exec:
            mock_submit = mock_exec.return_value.submit
            kick_dispatch()
            assert mock_submit.called

