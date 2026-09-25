"""Synchronous extraction orchestrator. Needs no Redis, Celery or worker.

    primary (Gemini) --ok--> validate --(shadow | low confidence | suspicious)--> OCR cross-check
         |fail/circuit open
         v
    fallback (OCR.space) -> deterministic parser -> validate (every row CHECK)
         |fail
         v
    FAILED with an owner-safe message; manual entry is always available

The owner always reviews the result. Nothing here writes live pricing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from django.conf import settings

from . import circuit, telemetry
from .crosscheck import crosscheck
from .errors import OWNER_MESSAGES, ProviderError, ProviderErrorKind
from .image import PreparedImage
from .matching import match_candidates
from .providers import ProviderResult, get_provider
from .schema import DraftCandidate
from .validation import assign_review_state, validate_extraction

OCR_MIN_SECONDS = 8
# Suspicious-signal row warnings that justify spending an OCR call.
_CROSSCHECK_TRIGGERS = {
    'AMBIGUOUS_PAIRING', 'PRICE_NOT_IN_SOURCE_TEXT', 'SUSPICIOUS_PRICE', 'LOW_CONFIDENCE',
    'CROSSED_OUT_PRICE', 'PARTIALLY_OBSCURED', 'CONFLICTING_DUPLICATE', 'AMBIGUOUS_DECIMAL_SEPARATOR',
}
_CROSSCHECK_DOC_TRIGGERS = {'BLURRY', 'GLARE', 'CROPPED', 'LOW_QUALITY', 'CONTAINS_INSTRUCTIONS'}


@dataclass
class ExtractionOutcome:
    ok: bool
    provider: str = ''
    model: str = ''
    candidates: list[DraftCandidate] = field(default_factory=list)
    document_warnings: list[str] = field(default_factory=list)
    currency: str = ''
    error_code: str = ''
    error_message: str = ''
    trace: dict = field(default_factory=dict)
    latency_ms: int = 0


def _usable(name: str | None):
    provider = get_provider(name)
    if provider is None or not provider.is_configured():
        return None, ProviderErrorKind.NOT_CONFIGURED
    if not provider.is_available():
        return None, ProviderErrorKind.QUOTA
    if circuit.is_open(provider.name):
        return None, ProviderErrorKind.CIRCUIT_OPEN
    return provider, None


def _attempt(provider, image: PreparedImage, timeout: float, trace: dict) -> ProviderResult | None:
    started = time.monotonic()
    entry = {'provider': provider.name}
    try:
        result = provider.extract(image, timeout=timeout)
    except ProviderError as err:
        entry.update(ok=False, kind=err.kind, status=err.status_code,
                     ms=int((time.monotonic() - started) * 1000))
        if getattr(err, 'attempts', None):
            entry['model_attempts'] = err.attempts
        trace['attempts'].append(entry)
        circuit.record_failure(provider.name, err.kind, daily_quota=getattr(err, 'daily_quota', False))
        telemetry.provider_failure(provider.name, '', err.kind, err.status_code, err.detail)
        return None
    circuit.record_success(provider.name)
    entry.update(ok=True, model=result.model, ms=result.latency_ms,
                 items=len(result.extraction.items) if result.extraction else 0,
                 **{k: v for k, v in result.usage.items() if k != 'attempts'})
    if result.usage.get('attempts'):
        entry['model_attempts'] = result.usage['attempts']
    trace['attempts'].append(entry)
    return result


def _shadow_active() -> bool:
    if not settings.PRICE_LIST_SHADOW_CROSSCHECK:
        return False
    from laundries.models.price_import import PriceListImportJob
    done = PriceListImportJob.objects.filter(
        status__in=[PriceListImportJob.Status.READY, PriceListImportJob.Status.CONFIRMED],
        served_from_cache=False,
    ).count()
    return done < settings.PRICE_LIST_SHADOW_CROSSCHECK_LIMIT


def run_extraction(image: PreparedImage, existing_items) -> ExtractionOutcome:
    started = time.monotonic()
    deadline = started + settings.PRICE_LIST_TOTAL_BUDGET_SECONDS
    trace: dict = {'attempts': [], 'skipped': {}}

    primary, why_primary = _usable(settings.PRICE_LIST_PRIMARY_PROVIDER)
    if why_primary:
        trace['skipped'][settings.PRICE_LIST_PRIMARY_PROVIDER] = why_primary

    primary_result = None
    if primary is not None:
        # Leave room for a fallback call inside the overall budget.
        budget = max(deadline - time.monotonic() - (OCR_MIN_SECONDS + 12), 10)
        budget = min(budget, settings.GEMINI_TIMEOUT_SECONDS) if primary.name == 'gemini' else budget
        primary_result = _attempt(primary, image, budget, trace)

    candidates: list[DraftCandidate] = []
    doc_warnings: list[str] = []
    currency = ''
    used = None
    if primary_result is not None and primary_result.extraction is not None:
        candidates, doc_warnings, currency = validate_extraction(
            primary_result.extraction, trusted_source=primary_result.structured,
        )
        used = primary_result

    # Decide whether the fallback runs: as a replacement, or as a cross-check.
    fallback_name = settings.PRICE_LIST_FALLBACK_PROVIDER
    need_replacement = used is None
    reasons = []
    if not need_replacement:
        if _shadow_active():
            reasons.append('shadow')
        if primary_result.extraction.document_confidence < 0.7:
            reasons.append('low_document_confidence')
        if any(_CROSSCHECK_TRIGGERS & set(c.warnings) for c in candidates):
            reasons.append('suspicious_rows')
        if _CROSSCHECK_DOC_TRIGGERS & set(doc_warnings):
            reasons.append('document_quality')
    fallback_result = None
    if (need_replacement or reasons) and fallback_name and fallback_name != (used.provider if used else ''):
        fallback, why_fallback = _usable(fallback_name)
        remaining = deadline - time.monotonic()
        if why_fallback:
            trace['skipped'][fallback_name] = why_fallback
        elif remaining < OCR_MIN_SECONDS:
            trace['skipped'][fallback_name] = 'NO_TIME_BUDGET'
        else:
            fallback_result = _attempt(fallback, image, remaining - 2, trace)

    if used is None and fallback_result is not None and fallback_result.extraction is not None:
        candidates, doc_warnings, currency = validate_extraction(
            fallback_result.extraction, trusted_source=fallback_result.structured,
        )
        used = fallback_result
        trace['fallback_used'] = True
    elif used is not None and fallback_result is not None:
        trace['crosscheck_reasons'] = reasons
        if fallback_result.raw_text is not None:
            summary = crosscheck(candidates, fallback_result.raw_text, currency)
            for w in summary.pop('doc_warnings'):
                if w not in doc_warnings:
                    doc_warnings.append(w)
            trace['crosscheck'] = summary
            for c in candidates:
                assign_review_state(c, currency_confirmed=bool(currency), trusted_source=True)
    elif used is not None and reasons:
        trace['crosscheck_reasons'] = reasons
        trace['crosscheck'] = 'unavailable'

    latency_ms = int((time.monotonic() - started) * 1000)
    trace['latency_ms'] = latency_ms
    if used is None:
        attempted = [a for a in trace['attempts']]
        only_input_errors = attempted and all(a.get('kind') == ProviderErrorKind.INVALID_INPUT for a in attempted)
        code = 'COULD_NOT_READ_IMAGE' if only_input_errors else 'AI_TEMPORARILY_UNAVAILABLE'
        telemetry.event('failed', code=code, latency_ms=latency_ms)
        return ExtractionOutcome(ok=False, error_code=code, error_message=OWNER_MESSAGES[code],
                                 trace=trace, latency_ms=latency_ms)

    if not candidates:
        code = 'NO_PRICES_FOUND'
        telemetry.event('no_items', provider=used.provider, latency_ms=latency_ms)
        return ExtractionOutcome(ok=False, provider=used.provider, model=used.model, error_code=code,
                                 error_message=OWNER_MESSAGES[code], document_warnings=doc_warnings,
                                 trace=trace, latency_ms=latency_ms)

    match_candidates(candidates, existing_items)
    telemetry.event(
        'extracted', provider=used.provider, model=used.model, items=len(candidates),
        latency_ms=latency_ms, fallback=bool(trace.get('fallback_used')),
        crosschecked=isinstance(trace.get('crosscheck'), dict),
    )
    return ExtractionOutcome(
        ok=True, provider=used.provider, model=used.model, candidates=candidates,
        document_warnings=doc_warnings, currency=currency, trace=trace, latency_ms=latency_ms,
    )
