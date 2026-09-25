"""Error taxonomy for price-list extraction.

Provider failures are classified once, at the provider boundary, so the
orchestrator can apply one retry/fallback policy and the API can show the owner
a plain-language message instead of a raw provider error.
"""
from __future__ import annotations


class ProviderErrorKind:
    INVALID_INPUT = 'INVALID_INPUT'   # 400: do not retry
    AUTH = 'AUTH'                     # 401/403: alert engineering, fall back
    QUOTA = 'QUOTA'                   # 429 / plan limit: fall back
    UNAVAILABLE = 'UNAVAILABLE'       # 5xx / overloaded: bounded retry, fall back
    TIMEOUT = 'TIMEOUT'
    SCHEMA = 'SCHEMA'                 # response did not validate
    NOT_CONFIGURED = 'NOT_CONFIGURED'
    CIRCUIT_OPEN = 'CIRCUIT_OPEN'
    NETWORK = 'NETWORK'

    RETRYABLE = frozenset({UNAVAILABLE, NETWORK})
    # Failures that say the provider (not the image) is unhealthy.
    TRIPS_CIRCUIT = frozenset({AUTH, QUOTA, UNAVAILABLE, TIMEOUT, NETWORK})


class ProviderError(Exception):
    """A classified provider failure. ``detail`` is for logs only, never owners,
    and must never contain credentials."""

    def __init__(self, kind: str, detail: str = '', *, status_code: int | None = None):
        super().__init__(f'{kind}: {detail}'[:300])
        self.kind = kind
        self.detail = detail[:300]
        self.status_code = status_code


class ImageRejected(Exception):
    """The upload is not an acceptable image. ``code`` maps to an API error."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


# Owner-facing copy. Never mention provider names or status codes.
OWNER_MESSAGES = {
    'AI_TEMPORARILY_UNAVAILABLE': (
        "We couldn't scan your price list right now. Please try again in a few "
        "minutes, or add your services manually."
    ),
    'COULD_NOT_READ_IMAGE': (
        "We couldn't read this image clearly. Try taking another photo in "
        "better lighting, with the whole price list in view."
    ),
    'NO_PRICES_FOUND': (
        "We couldn't find any services with prices in this image. Try another "
        "photo, or add your services manually."
    ),
}
