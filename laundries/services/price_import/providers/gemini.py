"""Gemini (google-genai SDK) structured price-list extraction. PRIMARY.

* Image + system instruction + response schema only: no tools, no search or
  maps grounding, no code execution, automatic function calling disabled.
* temperature 0 and low thinking: this is extraction, not writing.
* The key is always passed explicitly from ``settings.GEMINI_API_KEY``. The
  SDK would otherwise prefer ``GOOGLE_API_KEY`` from the environment, so an old
  key left there could silently take over.
* No SDK-level retries; the orchestrator owns the retry/fallback policy. The
  one in-provider retry is a hop to ``GEMINI_FALLBACK_MODEL`` when the primary
  model is overloaded (503) or retired (404). Those failures come back in
  about a second, so the hop costs little.
"""
from __future__ import annotations

import logging
import random
import time

from django.conf import settings

from ..errors import ProviderError, ProviderErrorKind
from ..image import PreparedImage
from ..prompts import SYSTEM_INSTRUCTION, USER_PROMPT
from ..schema import PriceListExtraction
from .base import PriceListExtractionProvider, ProviderResult

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 16384


def _classify(exc: Exception) -> ProviderError:
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, 'code', None) or 0
        status = (getattr(exc, 'status', '') or '').upper()
        message = (getattr(exc, 'message', '') or '')[:200]
        if code in (401, 403) or status in ('UNAUTHENTICATED', 'PERMISSION_DENIED'):
            kind = ProviderErrorKind.AUTH
        elif code == 429 or status == 'RESOURCE_EXHAUSTED':
            kind = ProviderErrorKind.QUOTA
        elif code == 404:
            kind = ProviderErrorKind.UNAVAILABLE      # model retired/unknown
        elif code == 408 or status == 'DEADLINE_EXCEEDED':
            kind = ProviderErrorKind.TIMEOUT
        elif code >= 500:
            kind = ProviderErrorKind.UNAVAILABLE
        else:
            kind = ProviderErrorKind.INVALID_INPUT
        err = ProviderError(kind, f'{code} {status} {message}', status_code=code)
        # A per-day quota (the free tier is 20 requests/day/model) will not
        # recover in minutes; let the circuit stay open much longer.
        err.daily_quota = kind == ProviderErrorKind.QUOTA and 'PerDay' in str(getattr(exc, 'details', '') or '')
        return err

    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return ProviderError(ProviderErrorKind.TIMEOUT, type(exc).__name__)
        if isinstance(exc, httpx.TransportError):
            return ProviderError(ProviderErrorKind.NETWORK, type(exc).__name__)
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, TimeoutError):
        return ProviderError(ProviderErrorKind.TIMEOUT, type(exc).__name__)
    return ProviderError(ProviderErrorKind.UNAVAILABLE, type(exc).__name__)


class GeminiPriceListProvider(PriceListExtractionProvider):
    name = 'gemini'
    structured = True

    def is_configured(self) -> bool:
        return bool(settings.PRICE_LIST_GEMINI_ENABLED and settings.GEMINI_API_KEY)

    def _client(self, timeout: float):
        from google import genai
        from google.genai import types

        return genai.Client(
            api_key=settings.GEMINI_API_KEY,
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=int(timeout * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def _config(self):
        from google.genai import types

        cfg = dict(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0,
            candidate_count=1,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type='application/json',
            response_schema=PriceListExtraction,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        level = settings.GEMINI_THINKING_LEVEL
        if level and level not in ('default', 'none'):
            cfg['thinking_config'] = types.ThinkingConfig(thinking_level=level)
        return types.GenerateContentConfig(**cfg)

    def _call(self, model: str, image: PreparedImage, timeout: float):
        from google.genai import types

        client = self._client(timeout)
        try:
            return client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=image.provider_bytes, mime_type=image.provider_mime),
                    USER_PROMPT,
                ],
                config=self._config(),
            )
        finally:
            try:
                client.close()
            except Exception:
                pass

    def extract(self, image: PreparedImage, *, timeout: float) -> ProviderResult:
        if not self.is_configured():
            raise ProviderError(ProviderErrorKind.NOT_CONFIGURED, 'GEMINI_API_KEY not set')

        models = [settings.GEMINI_MODEL]
        if settings.GEMINI_FALLBACK_MODEL and settings.GEMINI_FALLBACK_MODEL != settings.GEMINI_MODEL:
            models.append(settings.GEMINI_FALLBACK_MODEL)

        deadline = time.monotonic() + timeout
        last_error: ProviderError | None = None
        attempts = []
        for index, model in enumerate(models):
            remaining = deadline - time.monotonic()
            if remaining < 5:
                break
            if index > 0:
                time.sleep(random.uniform(0.2, 0.6))       # jitter before hop
            started = time.monotonic()
            try:
                response = self._call(model, image, remaining)
            except Exception as exc:  # classified below
                last_error = _classify(exc)
                attempts.append({'model': model, 'error': last_error.kind,
                                 'status': last_error.status_code,
                                 'ms': int((time.monotonic() - started) * 1000)})
                # Overload/retirement, or a per-model DAILY quota (each model
                # has its own), justify trying the other model. Auth, timeout
                # or a per-minute quota failure would just repeat.
                if last_error.kind == ProviderErrorKind.UNAVAILABLE and last_error.status_code in (503, 404, 500):
                    continue
                if getattr(last_error, 'daily_quota', False):
                    continue
                break
            latency_ms = int((time.monotonic() - started) * 1000)
            extraction = self._parse(response)
            usage = self._usage(response)
            usage['attempts'] = attempts + [{'model': model, 'ok': True, 'ms': latency_ms}]
            return ProviderResult(
                provider=self.name, model=model, extraction=extraction,
                latency_ms=latency_ms, usage=usage, structured=True,
            )
        err = last_error or ProviderError(ProviderErrorKind.TIMEOUT, 'no time left for Gemini')
        err.attempts = attempts
        raise err

    @staticmethod
    def _parse(response) -> PriceListExtraction:
        parsed = getattr(response, 'parsed', None)
        if isinstance(parsed, PriceListExtraction):
            return parsed
        text = getattr(response, 'text', None)
        if not text:
            reason = ''
            try:
                reason = str(response.candidates[0].finish_reason)
            except Exception:
                pass
            raise ProviderError(ProviderErrorKind.SCHEMA, f'empty response {reason}'[:120])
        try:
            return PriceListExtraction.model_validate_json(text)
        except Exception as exc:
            raise ProviderError(ProviderErrorKind.SCHEMA, f'invalid JSON: {type(exc).__name__}')

    @staticmethod
    def _usage(response) -> dict:
        meta = getattr(response, 'usage_metadata', None)
        if meta is None:
            return {}
        return {
            'prompt_tokens': getattr(meta, 'prompt_token_count', None),
            'output_tokens': getattr(meta, 'candidates_token_count', None),
            'thinking_tokens': getattr(meta, 'thoughts_token_count', None),
            'total_tokens': getattr(meta, 'total_token_count', None),
        }
