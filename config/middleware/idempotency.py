import hashlib
import json
# pyre-ignore[missing-module]
from django.core.cache import cache
# pyre-ignore[missing-module]
from django.http import JsonResponse, HttpResponse
# pyre-ignore[missing-module]
from django.utils.decorators import decorator_from_middleware

class IdempotencyMiddleware:
    """
    Middleware to prevent duplicate POST requests using X-Idempotency-Key headers.
    Stores the response in cache for a short duration (e.g., 24 hours).
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

        # Create a unique cache key based on the user and the provided key
        # If user is not authenticated, we use a global key (risky but okay for anon if needed)
        user_id = request.user.id if request.user.is_authenticated else "anon"
        cache_key = f"idempotency_{user_id}_{idempotency_key}"

        # Check if we have a cached response
        cached_response = cache.get(cache_key)
        if cached_response:
            return self.process_cached_response(cached_response)

        # Get the real response
        response = self.get_response(request)

        # Only cache successful or specific redirection responses to avoid caching errors
        if response.status_code in [200, 201, 202, 204]:
            self.cache_response(cache_key, response)

        return response

    def cache_response(self, cache_key, response):
        """Stores the response content and headers in cache."""
        try:
            # We only cache the content and status code for now
            # To be fully production-ready, we might want to cache headers too
            data = {
                "content": response.content.decode("utf-8") if isinstance(response.content, bytes) else response.content,
                "status_code": response.status_code,
                "content_type": response.get("Content-Type", "application/json")
            }
            cache.set(cache_key, data, 86400) # 24 hours
        except Exception:
            # If caching fails, don't break the request
            pass

    def process_cached_response(self, cached_data):
        """Constructs a response from cached data."""
        response = HttpResponse(
            content=cached_data["content"],
            status=cached_data["status_code"],
            content_type=cached_data["content_type"]
        )
        response["X-Idempotency-Cache"] = "HIT"
        return response
