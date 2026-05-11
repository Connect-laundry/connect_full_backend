from __future__ import annotations

from collections.abc import Mapping

SENSITIVE_KEY_TOKENS = (
    'authorization',
    'cookie',
    'password',
    'token',
    'secret',
    'phone',
    'address',
    'email',
    'metadata',
    'reference',
    'raw_response',
    'raw_body',
)


def mask_email(value: str | None) -> str:
    if not value:
        return '[REDACTED_EMAIL]'
    local, separator, domain = str(value).partition('@')
    if not separator:
        return '[REDACTED_EMAIL]'
    visible = local[:1] if local else '*'
    return f'{visible}***@{domain}'


def mask_phone(value: str | None) -> str:
    if not value:
        return '[REDACTED_PHONE]'
    digits = ''.join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 4:
        return '[REDACTED_PHONE]'
    return f'***{digits[-4:]}'


def mask_reference(value: str | None) -> str:
    if not value:
        return '[REDACTED_REFERENCE]'
    text = str(value)
    if len(text) <= 6:
        return '[REDACTED_REFERENCE]'
    return f'{text[:3]}***{text[-3:]}'


def summarize_exception(exc: Exception) -> str:
    return f'{exc.__class__.__name__}: {str(exc)[:180]}'


def redact_value(value):
    if isinstance(value, Mapping):
        redacted = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if 'email' in lowered:
                redacted[key] = mask_email(str(nested))
            elif 'phone' in lowered:
                redacted[key] = mask_phone(str(nested))
            elif 'reference' in lowered or lowered.endswith('_ref'):
                redacted[key] = mask_reference(str(nested))
            elif any(token in lowered for token in SENSITIVE_KEY_TOKENS):
                redacted[key] = '[REDACTED]'
            else:
                redacted[key] = redact_value(nested)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value
