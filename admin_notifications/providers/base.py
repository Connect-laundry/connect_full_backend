"""Provider-neutral send results and error classes.

The dispatcher only ever sees a ``SendResult``. Providers decide what an HTTP
status or exception means; the dispatcher decides what to do about it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ACCEPTED = 'accepted'
RETRYABLE = 'retryable'   # safe to try again: the request was not accepted
FAILED = 'failed'         # will not succeed until configuration/account changes
UNKNOWN = 'unknown'       # the provider may have accepted it; do not auto-resend

# Error classes (stored on the delivery, used as Sentry tags).
NETWORK = 'NETWORK'
PROVIDER_5XX = 'PROVIDER_5XX'
RATE_LIMITED = 'RATE_LIMITED'
TIMEOUT_AFTER_SEND = 'TIMEOUT_AFTER_SEND'
CONNECTION_LOST = 'CONNECTION_LOST'
MALFORMED_RESPONSE = 'MALFORMED_RESPONSE'
AUTH_FAILED = 'AUTH_FAILED'
INSUFFICIENT_BALANCE = 'INSUFFICIENT_BALANCE'
GATEWAY_INACTIVE = 'GATEWAY_INACTIVE'
INVALID_SENDER = 'INVALID_SENDER'
INVALID_RECIPIENT = 'INVALID_RECIPIENT'
TEMPLATE_INVALID = 'TEMPLATE_INVALID'
VALIDATION = 'VALIDATION'
NOT_FOUND = 'NOT_FOUND'
CONFIG_MISSING = 'CONFIG_MISSING'
PROVIDER_REJECTED = 'PROVIDER_REJECTED'
UNEXPECTED_4XX = 'UNEXPECTED_4XX'
WORKER_INTERRUPTED = 'WORKER_INTERRUPTED'
EXPIRED = 'EXPIRED'
RETRIES_EXHAUSTED = 'RETRIES_EXHAUSTED'
RENDER_ERROR = 'RENDER_ERROR'

# Failures that block the whole channel until someone fixes the account.
CHANNEL_BLOCKING = {AUTH_FAILED, INSUFFICIENT_BALANCE, GATEWAY_INACTIVE, INVALID_SENDER, CONFIG_MISSING,
                    TEMPLATE_INVALID, NOT_FOUND}


@dataclass(frozen=True)
class SendResult:
    outcome: str
    provider_message_id: str = ''
    error_class: str = ''
    safe_message: str = ''
    http_status: int | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome == ACCEPTED


_LONG_DIGITS = re.compile(r'\d{7,}')


def safe_provider_message(value, limit=200) -> str:
    """A provider error message fit for logs and admin screens.

    Long digit runs (phone numbers) are masked and the text is capped, so an
    echoed request never leaks recipient numbers or message content.
    """
    text = str(value or '').strip().replace('\n', ' ')
    text = _LONG_DIGITS.sub(lambda m: m.group(0)[:3] + '***', text)
    return text[:limit]
