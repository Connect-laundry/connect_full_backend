"""Backend Sentry routing: explicit environment, controlled check, quiet health."""
from unittest.mock import patch

import pytest


def test_environment_follows_the_service_not_the_sdk_default(monkeypatch):
    """Staging errors were tagged production because no environment was set."""
    import importlib
    import config.settings as project_settings
    monkeypatch.delenv('SENTRY_ENVIRONMENT', raising=False)
    monkeypatch.setenv('PUSH_ENVIRONMENT', 'staging')
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'abc1234')
    monkeypatch.delenv('SENTRY_DSN', raising=False)
    reloaded = importlib.reload(project_settings)
    try:
        assert reloaded.SENTRY_ENVIRONMENT == 'staging'
        assert reloaded.SENTRY_RELEASE == 'abc1234'
    finally:
        monkeypatch.undo()
        importlib.reload(project_settings)


def test_sentry_check_is_hidden_without_the_token(client, monkeypatch, settings):
    settings.IP_DIAGNOSTICS_ENABLED = False  # production
    monkeypatch.delenv('INTERNAL_HEALTH_TOKEN', raising=False)
    assert client.post('/health/sentry-check/').status_code == 404
    monkeypatch.setenv('INTERNAL_HEALTH_TOKEN', 'right-token')
    assert client.post('/health/sentry-check/', HTTP_X_HEALTH_TOKEN='wrong').status_code == 404
    assert client.get('/health/sentry-check/', HTTP_X_HEALTH_TOKEN='right-token').status_code == 404


def test_sentry_check_sends_one_info_event_and_reports_routing(client, monkeypatch, settings):
    monkeypatch.setenv('INTERNAL_HEALTH_TOKEN', 'right-token')
    settings.SENTRY_ENVIRONMENT = 'production'
    settings.SENTRY_RELEASE = 'deadbee'
    with patch('sentry_sdk.capture_message', return_value='evt123') as capture:
        response = client.post('/health/sentry-check/', HTTP_X_HEALTH_TOKEN='right-token')
    assert response.status_code == 200
    assert response.json() == {'sentry_enabled': True, 'event_id': 'evt123', 'environment': 'production', 'release': 'deadbee'}
    assert capture.call_args.kwargs['level'] == 'info'


@pytest.mark.django_db
def test_health_does_not_log_broker_errors_in_direct_mode(client, settings, monkeypatch, caplog):
    """No broker is needed in direct mode; logging it as an error on every
    health poll would flood Sentry once the production DSN is set."""
    settings.PUSH_USE_CELERY = False
    settings.CRITICAL_TASKS_USE_CELERY = False
    monkeypatch.delenv('CELERY_BROKER_URL', raising=False)
    monkeypatch.delenv('REDIS_URL', raising=False)
    client.get('/health/')
    assert not [r for r in caplog.records if 'Celery Broker is DOWN' in r.getMessage()]


def test_sentry_check_runs_on_staging_without_a_token(client, monkeypatch, settings):
    monkeypatch.delenv('INTERNAL_HEALTH_TOKEN', raising=False)
    settings.IP_DIAGNOSTICS_ENABLED = True  # staging only
    with patch('sentry_sdk.capture_message', return_value='evt'):
        assert client.post('/health/sentry-check/').status_code == 200
