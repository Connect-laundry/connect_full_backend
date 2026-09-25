"""OCR.space Engine 3 transcription. FALLBACK and CROSS-CHECK source.

Sends the separately compressed OCR copy (the free plan caps files at 1 MB), so
the Gemini copy never has to be degraded to fit. ``isTable=true`` keeps table
rows on one line with tab-separated cells, which the deterministic parser and
the cross-check rely on. No overlay: Engine 3 overlay adds latency and we do
not need word boxes.

Free-plan limits: 500 requests/day/IP, a separate 2,500 Engine 3 calls/month,
no SLA. Usage is counted internally (``circuit.record_ocr_call``) and the
provider reports itself unavailable near the monthly cap.
"""
from __future__ import annotations

import logging
import random
import time

import requests
from django.conf import settings

from .. import circuit
from ..errors import ProviderError, ProviderErrorKind
from ..image import PreparedImage
from ..text_parser import parse_text
from .base import PriceListExtractionProvider, ProviderResult

logger = logging.getLogger(__name__)

_QUOTA_HINTS = ('maximum', 'limit', 'quota', 'exceeded', 'too many')


class OCRSpaceProvider(PriceListExtractionProvider):
    name = 'ocr_space'
    structured = False

    def is_configured(self) -> bool:
        return bool(settings.PRICE_LIST_OCR_ENABLED and settings.OCR_SPACE_API_KEY)

    def is_available(self) -> bool:
        has_other_engine = settings.OCR_SPACE_FALLBACK_ENGINE not in ('', settings.OCR_SPACE_ENGINE)
        return self.is_configured() and (has_other_engine or circuit.ocr_quota_available())

    def _post(self, image: PreparedImage, timeout: float, engine: str):
        return requests.post(
            settings.OCR_SPACE_ENDPOINT,
            headers={'apikey': settings.OCR_SPACE_API_KEY},
            data={
                'OCREngine': engine,
                'language': 'auto',
                'isTable': 'true',
                'scale': 'true',
                'isOverlayRequired': 'false',
                'filetype': 'JPG',
            },
            files={'file': ('pricelist.jpg', image.ocr_bytes, 'image/jpeg')},
            timeout=(5, max(timeout - 5, 5)),
        )

    def _plan(self, timeout: float) -> list[tuple[str, float]]:
        """Engines to try, each with its own time cap.

        Engine 3 on the free plan allows one active request and was observed
        timing out server side after 60s (E563) while Engine 2 answered in
        ~1s. So Engine 3 gets a capped slice and a slow/overloaded Engine 3
        hops once to the fallback engine (separate, larger quota).
        """
        primary = settings.OCR_SPACE_ENGINE
        fallback = settings.OCR_SPACE_FALLBACK_ENGINE
        plan = [(primary, min(timeout, settings.OCR_SPACE_PRIMARY_ENGINE_TIMEOUT_SECONDS)
                 if fallback and fallback != primary else timeout)]
        if fallback and fallback != primary:
            plan.append((fallback, timeout))
        return plan

    def extract(self, image: PreparedImage, *, timeout: float) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ProviderErrorKind.NOT_CONFIGURED, 'OCR_SPACE_API_KEY not set')
        if not image.ocr_bytes:
            raise ProviderError(ProviderErrorKind.INVALID_INPUT, 'image could not be compressed under OCR size limit')

        deadline = time.monotonic() + timeout
        last_error: ProviderError | None = None
        attempts = []
        for index, (engine, cap) in enumerate(self._plan(timeout)):
            remaining = min(deadline - time.monotonic(), cap)
            if remaining < 5:
                break
            if engine == '3' and not circuit.ocr_quota_available():
                last_error = ProviderError(ProviderErrorKind.QUOTA, 'internal monthly Engine 3 budget reached')
                attempts.append({'engine': engine, 'error': 'QUOTA_BUDGET'})
                continue
            if index:
                time.sleep(random.uniform(0.2, 0.6))
            started = time.monotonic()
            if engine == '3':
                circuit.record_ocr_call()
            try:
                response = self._post(image, remaining, engine)
                result = self._handle(response, int((time.monotonic() - started) * 1000), engine)
            except requests.Timeout:
                last_error = ProviderError(ProviderErrorKind.TIMEOUT, f'engine {engine} timeout')
            except requests.RequestException as exc:
                last_error = ProviderError(ProviderErrorKind.NETWORK, type(exc).__name__)
            except ProviderError as err:
                last_error = err
            else:
                result.usage['attempts'] = attempts + [{'engine': engine, 'ok': True, 'ms': result.latency_ms}]
                return result
            attempts.append({'engine': engine, 'error': last_error.kind, 'status': last_error.status_code,
                             'ms': int((time.monotonic() - started) * 1000)})
            # Only a slow/overloaded/rate-limited engine justifies the other
            # engine; bad key or bad input would fail the same way.
            if last_error.kind not in (ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNAVAILABLE,
                                       ProviderErrorKind.QUOTA, ProviderErrorKind.NETWORK):
                break
        err = last_error or ProviderError(ProviderErrorKind.TIMEOUT, 'no time left for OCR.space')
        err.attempts = attempts
        raise err

    def _handle(self, response, latency_ms: int, engine: str) -> ProviderResult:
        code = response.status_code
        body_text = (response.text or '')[:300].lower()
        if code in (401, 403):
            kind = ProviderErrorKind.QUOTA if any(h in body_text for h in _QUOTA_HINTS) else ProviderErrorKind.AUTH
            raise ProviderError(kind, f'HTTP {code}', status_code=code)
        if code == 429:
            raise ProviderError(ProviderErrorKind.QUOTA, 'HTTP 429', status_code=code)
        if code >= 500:
            raise ProviderError(ProviderErrorKind.UNAVAILABLE, f'HTTP {code}', status_code=code)
        try:
            payload = response.json()
        except ValueError:
            # OCR.space returns a plain-text error body for some failures.
            if any(h in body_text for h in _QUOTA_HINTS):
                raise ProviderError(ProviderErrorKind.QUOTA, 'plain-text quota message', status_code=code)
            raise ProviderError(ProviderErrorKind.SCHEMA, 'non-JSON body', status_code=code)
        if not isinstance(payload, dict):
            raise ProviderError(ProviderErrorKind.SCHEMA, 'unexpected body', status_code=code)
        if payload.get('IsErroredOnProcessing'):
            message = payload.get('ErrorMessage') or ''
            if isinstance(message, list):
                message = ' '.join(str(m) for m in message)
            lowered = str(message).lower()
            if any(h in lowered for h in _QUOTA_HINTS):
                kind = ProviderErrorKind.QUOTA
            elif 'timed out' in lowered or 'timeout' in lowered:
                kind = ProviderErrorKind.TIMEOUT
            elif 'api key' in lowered or 'apikey' in lowered:
                kind = ProviderErrorKind.AUTH
            elif 'e500' in lowered or 'server' in lowered:
                kind = ProviderErrorKind.UNAVAILABLE
            else:
                kind = ProviderErrorKind.INVALID_INPUT
            raise ProviderError(kind, str(message)[:200], status_code=code)
        results = payload.get('ParsedResults') or []
        text = '\n'.join((r.get('ParsedText') or '') for r in results if isinstance(r, dict))
        return ProviderResult(
            provider=self.name,
            model=f'engine{engine}',
            extraction=parse_text(text),
            raw_text=text,
            latency_ms=latency_ms,
            usage={'processing_ms': payload.get('ProcessingTimeInMilliseconds')},
            structured=False,
        )
