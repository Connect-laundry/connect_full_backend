"""Structured telemetry for price-list import.

Logs carry sanitised fields only: provider, model, outcome, latency, token
counts. Never keys, headers, image bytes or extracted business text. Provider
failures that need engineering attention (auth, schema) also go to Sentry with
environment/provider/model tags, still without the image or raw payloads.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger('laundries.price_import')


def event(name: str, **fields) -> None:
    logger.info('price_import.%s', name, extra={'price_import': {'event': name, **fields}})


def provider_failure(provider: str, model: str, kind: str, status_code: int | None, detail: str) -> None:
    fields = {'provider': provider, 'model': model, 'kind': kind, 'status_code': status_code}
    # AUTH means a bad/revoked key: that is an incident, not noise.
    level = logging.ERROR if kind in ('AUTH', 'SCHEMA') else logging.WARNING
    logger.log(level, 'price_import provider failure %s/%s', provider, kind,
               extra={'price_import': {'event': 'provider_failure', **fields}})
    if kind not in ('AUTH', 'SCHEMA', 'QUOTA'):
        return
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            scope.set_tag('feature', 'price_list_import')
            scope.set_tag('provider', provider)
            scope.set_tag('model', model or 'n/a')
            scope.set_tag('failure_kind', kind)
            scope.set_tag('environment', getattr(settings, 'SENTRY_ENVIRONMENT', '') or 'unknown')
            scope.set_context('price_import', {'status_code': status_code, 'detail': detail[:200]})
            sentry_sdk.capture_message(
                f'price_list_import provider {kind.lower()} failure ({provider})',
                level='error' if kind != 'QUOTA' else 'warning',
            )
    except Exception:
        pass
