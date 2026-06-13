"""Pluggable OCR / vision provider layer for price-list import.

The import workflow (``laundries/views/price_import.py``) is provider-agnostic:
it calls :func:`get_ocr_provider` and persists whatever candidate items the
provider returns as unconfirmed drafts. A real provider (Google Vision, AWS
Textract, an LLM vision endpoint, ...) only needs to subclass
:class:`BaseOCRProvider` and be wired into :func:`get_ocr_provider` via the
``OCR_PROVIDER`` setting.

Until then the default :class:`NullOCRProvider` returns no candidates, so the
end-to-end flow (upload -> review -> confirm) is fully exercisable without any
external dependency or API key.
"""
from __future__ import annotations

import logging

# pyre-ignore[missing-module]
from django.conf import settings

logger = logging.getLogger(__name__)


class BaseOCRProvider:
    name = 'base'

    def extract(self, image_file) -> list[dict]:
        """Return a list of candidate dicts.

        Each dict may contain: ``item_name`` (str, required), ``suggested_price``
        (number|None), ``category`` (str), ``confidence`` (float 0..1|None).
        """
        raise NotImplementedError


class NullOCRProvider(BaseOCRProvider):
    """Default provider: extracts nothing (no OCR configured)."""

    name = 'null'

    def extract(self, image_file) -> list[dict]:
        logger.info("NullOCRProvider used; no items extracted from price list image.")
        return []


def get_ocr_provider() -> BaseOCRProvider:
    """Resolve the configured OCR provider.

    Reads ``settings.OCR_PROVIDER``. Unknown/unset values fall back to the null
    provider so the workflow degrades gracefully.
    """
    provider = (getattr(settings, 'OCR_PROVIDER', '') or '').lower()
    # Real providers register here, e.g.:
    #   if provider == 'google_vision': return GoogleVisionOCRProvider()
    if provider in ('', 'none', 'null'):
        return NullOCRProvider()
    logger.warning("Unknown OCR_PROVIDER %r; falling back to NullOCRProvider.", provider)
    return NullOCRProvider()
