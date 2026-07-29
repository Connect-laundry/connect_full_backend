"""Idempotency middleware.

The guarantee must hold across gunicorn workers, so the store is the database,
not the cache — a per-process ``LocMemCache`` (the fallback whenever Redis is
absent) would let two identical payment requests through on two workers.
"""
import hashlib
import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory

from config.middleware.idempotency import IdempotencyMiddleware
from marketplace.models import IdempotencyRecord


def _post(key='key-1', body=None, path='/api/v1/payments/initialize/'):
    payload = json.dumps(body if body is not None else {'order_id': 'abc'})
    request = RequestFactory().post(
        path, data=payload, content_type='application/json',
        HTTP_X_IDEMPOTENCY_KEY=key,
    )
    request.user = AnonymousUser()
    return request


@pytest.mark.django_db
class TestIdempotencyMiddleware:
    def test_first_request_passes_through_and_is_recorded(self):
        calls = []

        def view(request):
            calls.append(1)
            return JsonResponse({'ok': True}, status=201)

        response = IdempotencyMiddleware(view)(_post())

        assert response.status_code == 201
        assert len(calls) == 1
        record = IdempotencyRecord.objects.get()
        assert record.status_code == 201
        assert record.is_complete

    def test_repeat_request_replays_without_re_running_the_view(self):
        calls = []

        def view(request):
            calls.append(1)
            return JsonResponse({'order': 'created'}, status=201)

        middleware = IdempotencyMiddleware(view)
        first = middleware(_post())
        second = middleware(_post())

        assert first.status_code == second.status_code == 201
        # The view must run exactly once — this is the duplicate-charge guard.
        assert len(calls) == 1
        assert second['X-Idempotency-Cache'] == 'HIT'
        assert json.loads(second.content) == {'order': 'created'}

    def test_survives_a_cache_wipe(self):
        """A worker restart or Redis outage must not reopen the key."""
        middleware = IdempotencyMiddleware(lambda r: JsonResponse({'ok': True}, status=200))
        middleware(_post())

        cache.clear()

        calls = []

        def view(request):
            calls.append(1)
            return JsonResponse({'ok': True}, status=200)

        second = IdempotencyMiddleware(view)(_post())

        assert second['X-Idempotency-Cache'] == 'HIT'
        assert calls == []

    def test_same_key_with_a_different_body_is_rejected(self):
        middleware = IdempotencyMiddleware(lambda r: JsonResponse({'ok': True}, status=200))
        middleware(_post(body={'order_id': 'first'}))

        response = middleware(_post(body={'order_id': 'second'}))

        assert response.status_code == 409
        assert 'different request' in json.loads(response.content)['message']

    def test_in_flight_duplicate_gets_409(self):
        """A second worker must not proceed while the first is still running."""
        request = _post()
        # Simulate the concurrent request: the claim exists but has no response.
        IdempotencyRecord.objects.create(
            key='anon:127.0.0.1:key-1',
            fingerprint=hashlib.sha256(
                f"POST:/api/v1/payments/initialize/:"
                f"{hashlib.sha256(request.body).hexdigest()}".encode('utf-8')
            ).hexdigest(),
        )

        calls = []

        def view(r):
            calls.append(1)
            return JsonResponse({'ok': True}, status=200)

        response = IdempotencyMiddleware(view)(request)

        assert response.status_code == 409
        assert calls == []

    def test_failed_request_releases_the_key_for_retry(self):
        attempts = []

        def flaky(request):
            attempts.append(1)
            status = 500 if len(attempts) == 1 else 201
            return JsonResponse({'attempt': len(attempts)}, status=status)

        middleware = IdempotencyMiddleware(flaky)
        first = middleware(_post())
        second = middleware(_post())

        assert first.status_code == 500
        assert second.status_code == 201
        # The retry must actually run, not replay the failure.
        assert len(attempts) == 2

    def test_exception_releases_the_key(self):
        def boom(request):
            raise RuntimeError('view exploded')

        with pytest.raises(RuntimeError):
            IdempotencyMiddleware(boom)(_post())

        assert not IdempotencyRecord.objects.exists()

    def test_requests_without_the_header_are_untouched(self):
        request = RequestFactory().post(
            '/api/v1/payments/initialize/', data='{}', content_type='application/json')
        request.user = AnonymousUser()

        response = IdempotencyMiddleware(lambda r: JsonResponse({'ok': True}))(request)

        assert response.status_code == 200
        assert not IdempotencyRecord.objects.exists()

    def test_get_requests_are_untouched(self):
        request = RequestFactory().get('/api/v1/orders/', HTTP_X_IDEMPOTENCY_KEY='key-1')
        request.user = AnonymousUser()

        IdempotencyMiddleware(lambda r: JsonResponse({'ok': True}))(request)

        assert not IdempotencyRecord.objects.exists()

    def test_different_clients_can_use_the_same_key(self):
        """Keys are scoped per caller, so one client cannot block another."""
        middleware = IdempotencyMiddleware(lambda r: JsonResponse({'ok': True}, status=200))

        first = _post()
        first.META['REMOTE_ADDR'] = '10.0.0.1'
        second = _post()
        second.META['REMOTE_ADDR'] = '10.0.0.2'

        assert middleware(first).status_code == 200
        response = middleware(second)

        assert response.status_code == 200
        assert 'X-Idempotency-Cache' not in response
        assert IdempotencyRecord.objects.count() == 2
