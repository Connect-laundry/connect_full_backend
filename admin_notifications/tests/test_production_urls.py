"""Production admin links and SMS callback origins: explicit, https, never staging."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from admin_notifications import conf
from admin_notifications.checks import ERROR, validate_configuration
from admin_notifications.models import AdminNotificationEvent as Event
from admin_notifications.snapshot import ADMIN_LINK_UNAVAILABLE, admin_order_url
from admin_notifications.tests.helpers import book, configure, make_world

STAGING = 'https://connect-full-backend.onrender.com'
PROD = 'https://connect-full-backend-production.onrender.com'


@pytest.mark.parametrize('value,problem', [
    ('', 'not set'),
    ('http://connect-full-backend-production.onrender.com', 'https'),
    (STAGING, 'not the production backend'),
    ('https://localhost:8000', 'non-production'),
    ('https://api.simame.test', 'non-production'),
    (PROD + '/admin', 'origin only'),
])
def test_bad_production_admin_origins_fail_preflight(settings, value, problem):
    configure(settings)
    settings.ADMIN_NOTIFICATION_ADMIN_BASE_URL = value
    errors = [m for level, m in validate_configuration() if level == ERROR]
    assert any('ADMIN_NOTIFICATION_ADMIN_BASE_URL' in m and problem in m for m in errors), errors


def test_production_never_falls_back_to_admin_base_url(settings):
    configure(settings)
    settings.ADMIN_NOTIFICATION_ADMIN_BASE_URL = ''
    settings.ADMIN_BASE_URL = STAGING  # the real non-DEBUG default
    assert conf.admin_base_url() == ''
    assert admin_order_url('abc') == ADMIN_LINK_UNAVAILABLE
    settings.ADMIN_NOTIFICATION_ADMIN_BASE_URL = STAGING
    assert admin_order_url('abc') == ADMIN_LINK_UNAVAILABLE


def test_production_callback_never_falls_back_to_staging(settings):
    configure(settings)
    settings.ARKESEL_SMS_CALLBACK_BASE_URL = ''
    settings.ADMIN_BASE_URL = STAGING
    assert conf.arkesel_callback_url() == ''
    assert any('ARKESEL_SMS_CALLBACK_BASE_URL' in m for lvl, m in validate_configuration() if lvl == ERROR)


def test_non_production_may_use_admin_base_url(settings):
    configure(settings, environment='staging')
    settings.ADMIN_NOTIFICATION_ADMIN_BASE_URL = ''
    settings.ADMIN_BASE_URL = 'https://staging-backend.example.com'
    assert admin_order_url('abc') == 'https://staging-backend.example.com/admin/ordering/order/abc/change/'


@pytest.mark.django_db
def test_production_alert_link_is_the_production_admin_and_requires_login(settings):
    configure(settings)
    settings.ADMIN_NOTIFICATION_ADMIN_BASE_URL = PROD
    order_id = book(make_world()).data['id']
    url = Event.objects.get(order_id=order_id).payload['admin_url']
    assert url == f'{PROD}/admin/ordering/order/{order_id}/change/'
    out = StringIO()
    call_command('check_admin_notifications', stdout=out)
    assert '[ERROR]' not in out.getvalue() and PROD in out.getvalue()
    # The path behind the link is Django admin: anonymous users are sent to login.
    path = reverse('admin:ordering_order_change', args=[order_id])
    response = Client().get(path)
    assert response.status_code == 302 and '/admin/login/' in response['Location']
