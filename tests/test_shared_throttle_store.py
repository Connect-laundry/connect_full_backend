"""Abuse-sensitive limits are shared across workers without Redis.

Production has no Redis: every Gunicorn worker kept its own counters, so a
password-guessing burst spread over workers got workers x the configured
attempts. Sensitive throttles now count in the shared 'throttle' cache, a
Postgres table when there is no Redis.
"""
import pytest
from django.core.cache import caches
from django.core.management import call_command
from django.urls import reverse
from django.utils.connection import ConnectionProxy

from users.models import User


@pytest.fixture
def db_throttle_store(settings, django_db_blocker):
    settings.CACHES = {
        **settings.CACHES,
        'throttle': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': 'simame_throttle_cache_test',
            'OPTIONS': {'MAX_ENTRIES': 100000, 'CULL_FREQUENCY': 10},
        },
    }
    caches._settings = None  # re-read CACHES
    if 'throttle' in caches._connections.__dict__:
        del caches['throttle']  # drop an instance created before this test
    with django_db_blocker.unblock():
        call_command('createcachetable', 'simame_throttle_cache_test', verbosity=0)
    yield caches['throttle']
    caches['throttle'].clear()
    del caches['throttle']
    caches._settings = None


@pytest.mark.django_db(transaction=True)
def test_login_account_limit_counts_across_workers(client, db_throttle_store):
    from config.throttling import LoginAccountBurstThrottle
    limit = LoginAccountBurstThrottle().num_requests  # login_account_burst, 10 per 5 min by default
    User.objects.create_user(email='victim@example.com', phone='233200000960', password='Right-Pass-123')
    statuses = []
    for _ in range(limit + 1):
        caches['default'].clear()  # the next request lands on a different worker
        response = client.post(reverse('auth_login'), {'email': 'victim@example.com', 'password': 'wrong'},
                               content_type='application/json')
        statuses.append(response.status_code)
    assert statuses == [401] * limit + [429], statuses


def test_sensitive_throttles_use_shared_store_and_general_budget_stays_local():
    from config.throttling import (
        BurstUserThrottle, LoginAccountBurstThrottle, RegisterIPBurstThrottle, SustainedUserThrottle,
    )
    for cls in (LoginAccountBurstThrottle, RegisterIPBurstThrottle):
        assert isinstance(cls.cache, ConnectionProxy) and cls.cache._alias == 'throttle', cls
    for cls in (BurstUserThrottle, SustainedUserThrottle):
        assert cls.cache._alias == 'default', cls


def test_production_settings_share_counters_without_redis():
    from django.conf import settings as live
    assert 'throttle' in live.CACHES
