import hashlib
import logging
# pyre-ignore[missing-module]
from django.core.cache import cache
# pyre-ignore[missing-module]
from django.http import JsonResponse, HttpResponse
# pyre-ignore[missing-module]
from django.utils.decorators import decorator_from_middleware

logger = logging.getLogger(__name__)

class IdempotencyMiddleware:
    """
    Middleware to prevent duplicate POST requests using X-Idempotency-Key headers.
    Stores the response in cache for a short duration and rejects key reuse
    across different routes or request bodies.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only apply to POST requests that have the header
        if request.method != "POST":
            return self.get_response(request)

        idempotency_key = request.headers.get("X-Idempotency-Key")
        if not idempotency_key:
            return self.get_response(request)

        client_ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', 'unknown'))
        user_id = request.user.id if request.user.is_authenticated else f"anon:{client_ip}"
        request_body = request.body or b""
        request_hash = hashlib.sha256(request_body).hexdigest()
        request_fingerprint = hashlib.sha256(
            f"{request.method}:{request.path}:{request_hash}".encode("utf-8")
        ).hexdigest()
        cache_key = f"idempotency_{user_id}_{idempotency_key}"

        cached_response = cache.get(cache_key)
        if cached_response:
            if cached_response.get("fingerprint") != request_fingerprint:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "This idempotency key was already used for a different request.",
                        "data": {},
                    },
                    status=409,
                )
            return self.process_cached_response(cached_response)

        response = self.get_response(request)

        if response.status_code in [200, 201, 202, 204]:
            self.cache_response(cache_key, response, request_fingerprint)

        return response

    def cache_response(self, cache_key, response, request_fingerprint):
        """Stores the response content and headers in cache."""
        try:
            if hasattr(response, 'render') and callable(response.render):
                response.render()
            data = {
                "content": response.content.decode("utf-8") if isinstance(response.content, bytes) else response.content,
                "status_code": response.status_code,
                "content_type": response.get("Content-Type", "application/json"),
                "fingerprint": request_fingerprint,
            }
            cache.set(cache_key, data, 86400)
        except Exception as exc:
            # Failing to cache must not fail the request, but stay visible.
            logger.warning(
                "Could not cache idempotent response",
                extra={"cache_key": cache_key, "error": str(exc)},
            )

    def process_cached_response(self, cached_data):
        """Constructs a response from cached data."""
        response = HttpResponse(
            content=cached_data["content"],
            status=cached_data["status_code"],
            content_type=cached_data["content_type"]
        )
        response["X-Idempotency-Cache"] = "HIT"
        return response
