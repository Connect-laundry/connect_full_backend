"""Graceful-degradation tests: a database outage must return a structured 503,
never a raw HTTP 500. Covers the three interception points added for the
Neon -> Supabase migration."""
import json

import pytest
from django.db.utils import InterfaceError, OperationalError
from django.test import RequestFactory

from config.exception_handler import custom_exception_handler
from config.middleware.database_availability import DatabaseAvailabilityMiddleware
from config.middleware.deactivation import DeactivationMiddleware
from config.resilience import database_unavailable_response, is_database_unavailable

pytestmark = pytest.mark.django_db  # allow DB but we never actually touch it


@pytest.fixture
def rf():
    return RequestFactory()


def test_is_database_unavailable_classifier():
    assert is_database_unavailable(OperationalError("connection failed"))
    assert is_database_unavailable(InterfaceError("connection already closed"))
    assert not is_database_unavailable(ValueError("nope"))


def test_response_is_json_for_api_paths(rf):
    request = rf.get('/api/v1/orders/')
    response = database_unavailable_response(request)
    assert response.status_code == 503
    assert response['Retry-After'] == '15'
    assert response['Cache-Control'] == 'no-store'
    body = json.loads(response.content)
    assert body['status'] == 'error'
    assert 'temporarily unavailable' in body['message'].lower()


def test_response_is_html_for_browser_paths(rf):
    request = rf.get('/admin/')
    response = database_unavailable_response(request)
    assert response.status_code == 503
    assert response['Content-Type'].startswith('text/html')
    assert b'Temporarily unavailable' in response.content


def test_drf_handler_returns_503_on_db_error(rf):
    request = rf.get('/api/v1/laundries/')
    request.request_id = 'req-abc'
    response = custom_exception_handler(OperationalError("server closed"), {'request': request})
    assert response.status_code == 503
    assert response['Retry-After'] == '15'
    assert response.data['status'] == 'error'
    assert response.data['request_id'] == 'req-abc'


def test_drf_handler_leaves_non_db_errors_alone(rf):
    request = rf.get('/api/v1/laundries/')
    # A plain ValueError is not a DRF-known exception -> handler returns 500 envelope.
    response = custom_exception_handler(ValueError("bug"), {'request': request})
    assert response.status_code == 500


def test_availability_middleware_catches_db_error(rf):
    mw = DatabaseAvailabilityMiddleware(lambda r: None)
    request = rf.get('/admin/')
    response = mw.process_exception(request, OperationalError("down"))
    assert response is not None
    assert response.status_code == 503


def test_availability_middleware_ignores_other_errors(rf):
    mw = DatabaseAvailabilityMiddleware(lambda r: None)
    request = rf.get('/admin/')
    assert mw.process_exception(request, ValueError("bug")) is None


class _ExplodingUser:
    """Simulates request.user whose session lookup hits a dead database."""
    @property
    def is_authenticated(self):
        raise OperationalError("connection failed")


def test_deactivation_middleware_degrades_on_db_outage(rf):
    mw = DeactivationMiddleware(lambda r: None)
    request = rf.get('/admin/')
    request.user = _ExplodingUser()
    response = mw.process_request(request)
    assert response is not None
    assert response.status_code == 503
