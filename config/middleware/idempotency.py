import hashlib
import logging
from datetime import timedelta

# pyre-ignore[missing-module]
from django.db import IntegrityError, transaction
# pyre-ignore[missing-module]
from django.http import JsonResponse, HttpResponse
# pyre-ignore[missing-module]
from django.utils import timezone

logger = logging.getLogger(__name__)

# How long a key is honoured. Matches the previous cache TTL.
RETENTION = timedelta(hours=24)


class IdempotencyMiddleware:
    """Prevent duplicate POSTs carrying an ``X-Idempotency-Key``.

    The key is *claimed* in the database before the view runs, so two identical
    requests can never both execute — even when they land on different gunicorn
    workers or different instances. The second one gets the first one's
    response (or a 409 while it is still in flight).

    A claim is released if the request fails, so a client can safely retry
    after a 5xx; keeping it would strand the caller with an empty replay.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST":
            return self.get_response(request)

        idempotency_key = request.headers.get("X-Idempotency-Key")
        if not idempotency_key:
            return self.get_response(request)

        # Imported lazily: middleware is constructed before the app registry
        # is ready.
        from marketplace.models import IdempotencyRecord

        client_ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', 'unknown'))
        user_id = request.user.id if request.user.is_authenticated else f"anon:{client_ip}"
        request_hash = hashlib.sha256(request.body or b"").hexdigest()
        fingerprint = hashlib.sha256(
            f"{request.method}:{request.path}:{request_hash}".encode("utf-8")
        ).hexdigest()
        key = f"{user_id}:{idempotency_key}"

        try:
            record, claimed = self._claim(IdempotencyRecord, key, fingerprint)
        except Exception as exc:
            # A storage failure must not take the endpoint down; fall through
            # to normal (non-idempotent) handling and stay visible in logs.
            logger.warning(
                "Idempotency claim failed; processing without protection",
                extra={"error": str(exc)},
            )
            return self.get_response(request)

        if not claimed:
            if record.fingerprint != fingerprint:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "This idempotency key was already used for a different request.",
                        "data": {},
                    },
                    status=409,
                )
            if not record.is_complete:
                # The original is still running. Retrying is safe once it
                # finishes, so tell the client to come back.
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "An identical request is already being processed.",
                        "data": {},
                    },
                    status=409,
                )
            return self._replay(record)

        try:
            response = self.get_response(request)
        except Exception:
            self._release(IdempotencyRecord, key)
            raise

        if response.status_code in (200, 201, 202, 204):
            self._store(IdempotencyRecord, key, response)
        else:
            # Let the client retry a failed request with the same key.
            self._release(IdempotencyRecord, key)

        return response

    # ------------------------------------------------------------------ io

    @staticmethod
    def _claim(model, key, fingerprint):
        """Reserve ``key``. Returns (record, claimed_by_us)."""
        cutoff = timezone.now() - RETENTION

        existing = model.objects.filter(key=key).first()
        if existing is not None:
            if existing.created_at < cutoff:
                # Expired: take it over rather than rejecting a valid retry.
                existing.delete()
            else:
                return existing, False

        try:
            with transaction.atomic():
                record = model.objects.create(key=key, fingerprint=fingerprint)
            return record, True
        except IntegrityError:
            # Lost the race to a concurrent identical request.
            return model.objects.filter(key=key).first(), False

    @staticmethod
    def _release(model, key):
        try:
            model.objects.filter(key=key, status_code__isnull=True).delete()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not release idempotency claim", extra={"error": str(exc)})

    @staticmethod
    def _store(model, key, response):
        try:
            if hasattr(response, 'render') and callable(response.render):
                response.render()
            content = response.content
            model.objects.filter(key=key).update(
                status_code=response.status_code,
                content_type=response.get("Content-Type", "application/json"),
                content=content.decode("utf-8") if isinstance(content, bytes) else content,
            )
        except Exception as exc:
            logger.warning(
                "Could not persist idempotent response",
                extra={"key": key, "error": str(exc)},
            )

    @staticmethod
    def _replay(record):
        response = HttpResponse(
            content=record.content,
            status=record.status_code,
            content_type=record.content_type or "application/json",
        )
        response["X-Idempotency-Cache"] = "HIT"
        return response
