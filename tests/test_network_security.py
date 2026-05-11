import os
import json
import hashlib
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import JsonResponse
from django.test import RequestFactory, override_settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from config.middleware.idempotency import IdempotencyMiddleware
from config.middleware.security import SecurityHeadersMiddleware
from users.models import User


@pytest.mark.django_db
class TestNetworkSecurity:
    def test_public_health_endpoint_is_sanitized(self):
        with override_settings(ROOT_URLCONF='config.urls'):
            client = APIClient()
            response = client.get('/health/')

            assert response.status_code in {status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE}
            assert set(response.json().keys()) == {'status'}
            assert 'components' not in response.json()

    def test_internal_health_endpoint_can_return_component_state(self):
        with mock.patch.dict(os.environ, {'INTERNAL_HEALTH_TOKEN': 'health-secret'}, clear=False):
            with override_settings(ROOT_URLCONF='config.urls'):
                client = APIClient()
                response = client.get('/health/', HTTP_X_HEALTH_TOKEN='health-secret')

                assert response.status_code in {status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE}
                payload = response.json()
                assert 'status' in payload
                assert 'components' in payload

    def test_request_id_is_echoed_back_to_clients(self):
        with override_settings(ROOT_URLCONF='config.urls'):
            client = APIClient()
            response = client.get('/health/', HTTP_X_REQUEST_ID='audit-request-123', HTTP_HOST='localhost')

            assert response.status_code in {status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE}
            assert response['X-Request-ID'] == 'audit-request-123'

    def test_idempotency_keys_cannot_be_reused_for_different_requests(self):
        request_factory = RequestFactory()
        middleware = IdempotencyMiddleware(lambda request: JsonResponse({'ok': True}, status=200))

        cache.clear()

        original_body = json.dumps({'email': 'first@example.com', 'password': 'StrongPass123!'}).encode('utf-8')
        original_hash = hashlib.sha256(original_body).hexdigest()
        original_fingerprint = hashlib.sha256(
            f"POST:/api/v1/auth/login/:{original_hash}".encode('utf-8')
        ).hexdigest()
        cache.set(
            'idempotency_anon:127.0.0.1_login-key-1',
            {
                'content': json.dumps({'ok': True}),
                'status_code': 200,
                'content_type': 'application/json',
                'fingerprint': original_fingerprint,
            },
            86400,
        )

        request = request_factory.post(
            '/api/v1/auth/login/',
            data=json.dumps({'email': 'second@example.com', 'password': 'WrongPass123!'}),
            content_type='application/json',
            HTTP_X_IDEMPOTENCY_KEY='login-key-1',
        )
        request.user = AnonymousUser()

        response = middleware(request)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert json.loads(response.content)['message'] == (
            'This idempotency key was already used for a different request.'
        )

    def test_local_schema_docs_get_dev_only_csp_for_swagger_assets(self):
        request_factory = RequestFactory()
        middleware = SecurityHeadersMiddleware(lambda request: JsonResponse({'ok': True}))
        request = request_factory.get('/api/schema/swagger-ui/')

        with override_settings(DEBUG=True):
            response = middleware.process_response(request, JsonResponse({'ok': True}))

        csp = response['Content-Security-Policy']
        assert "default-src 'self'" in csp
        assert 'cdn.jsdelivr.net' in csp
        assert "frame-ancestors 'none'" in csp
        assert csp != "default-src 'none'; frame-ancestors 'none'"

    def test_production_api_csp_stays_locked_down(self):
        request_factory = RequestFactory()
        middleware = SecurityHeadersMiddleware(lambda request: JsonResponse({'ok': True}))
        request = request_factory.get('/api/v1/orders/')

        with override_settings(DEBUG=False):
            response = middleware.process_response(request, JsonResponse({'ok': True}))

        assert response['Content-Security-Policy'] == "default-src 'none'; frame-ancestors 'none'"
