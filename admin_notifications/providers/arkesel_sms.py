"""ArkeselSmsProvider: Arkesel SMS API v2.

Contract (official spec developers.arkesel.com, api_spec v2.4.0):

    POST https://sms.arkesel.com/api/v2/sms/send
    header  api-key: <key>
    body    {"sender", "message", "recipients": [...], "callback_url"?, "sandbox"?}
    200     {"status": "success", "data": [{"recipient": "233...", "id": "<uuid>"}]}
    401 authentication failed, 402 insufficient balance, 403 inactive gateway,
    422 validation errors, 500 internal error

A 200 only means Arkesel accepted the message. Handset delivery arrives later
on ``callback_url`` as ``?sms_id=<id>&status=DELIVERED|...``.
"""
from __future__ import annotations

from .. import conf
from . import base
from .http import CONNECT_FAILED, post_json

PROVIDER = 'arkesel_sms'
SEND_PATH = '/api/v2/sms/send'


def transport_result(outcome) -> base.SendResult | None:
    """Shared mapping for transport failures and generic HTTP classes."""
    if outcome.transport_error == CONNECT_FAILED:
        return base.SendResult(base.RETRYABLE, error_class=base.NETWORK,
                               safe_message='Could not connect to provider (DNS/connection failure).')
    if outcome.transport_error:
        return base.SendResult(
            base.UNKNOWN,
            error_class=base.TIMEOUT_AFTER_SEND if outcome.transport_error == 'read_timeout' else base.CONNECTION_LOST,
            safe_message='Request sent but no usable response; the provider may have accepted it.',
            http_status=outcome.status_code,
        )
    status = outcome.status_code or 0
    if status == 429:
        return base.SendResult(base.RETRYABLE, error_class=base.RATE_LIMITED, safe_message='Provider rate limit.',
                               http_status=status)
    if status >= 500:
        message = outcome.body.get('message') if isinstance(outcome.body, dict) else ''
        return base.SendResult(base.RETRYABLE, error_class=base.PROVIDER_5XX,
                               safe_message=base.safe_provider_message(message) or f'Provider HTTP {status}.',
                               http_status=status)
    return None


class ArkeselSmsProvider:
    name = PROVIDER

    def missing_config(self) -> list[str]:
        missing = []
        if not conf.arkesel_api_key():
            missing.append('ARKESEL_API_KEY')
        sender = conf.arkesel_sender_id()
        if not sender or len(sender) > 11:
            missing.append('ARKESEL_SENDER_ID (1-11 characters)')
        return missing

    def build_request(self, recipient: str, message: str) -> tuple[str, dict, dict]:
        payload = {
            'sender': conf.arkesel_sender_id(),
            'message': message,
            'recipients': [recipient],
        }
        callback = conf.arkesel_callback_url()
        if callback:
            payload['callback_url'] = callback
        if conf.arkesel_sms_sandbox():
            payload['sandbox'] = True
        headers = {'api-key': conf.arkesel_api_key()}
        return conf.arkesel_sms_base_url() + SEND_PATH, headers, payload

    def send(self, recipient: str, message: str) -> base.SendResult:
        missing = self.missing_config()
        if missing:
            return base.SendResult(base.FAILED, error_class=base.CONFIG_MISSING,
                                   safe_message='Missing: ' + ', '.join(missing))
        url, headers, payload = self.build_request(recipient, message)
        outcome = post_json(url, headers=headers, payload=payload, timeout=conf.http_timeouts())
        return self.interpret(outcome, recipient)

    @staticmethod
    def interpret(outcome, recipient: str) -> base.SendResult:
        generic = transport_result(outcome)
        if generic is not None:
            return generic
        status = outcome.status_code or 0
        body = outcome.body if isinstance(outcome.body, dict) else {}
        message = base.safe_provider_message(body.get('message'))

        if 200 <= status < 300:
            if not outcome.json_ok:
                return base.SendResult(base.UNKNOWN, error_class=base.MALFORMED_RESPONSE,
                                       safe_message='HTTP 2xx with an unreadable body.', http_status=status)
            if str(body.get('status', '')).lower() != 'success':
                return base.SendResult(base.FAILED, error_class=base.PROVIDER_REJECTED,
                                       safe_message=message or 'Provider did not report success.',
                                       http_status=status)
            entries = body.get('data') if isinstance(body.get('data'), list) else []
            ids = [
                e for e in entries
                if isinstance(e, dict) and e.get('id') and str(e.get('recipient', '')).lstrip('+') == recipient
            ]
            if not ids:
                ids = [e for e in entries if isinstance(e, dict) and e.get('id')]
            if ids:
                return base.SendResult(base.ACCEPTED, provider_message_id=str(ids[0]['id'])[:128],
                                       http_status=status)
            if any(isinstance(e, dict) and e.get('invalid numbers') for e in entries):
                return base.SendResult(base.FAILED, error_class=base.INVALID_RECIPIENT,
                                       safe_message='Arkesel rejected the recipient number.', http_status=status)
            # Accepted without a message id: no delivery report can be matched.
            return base.SendResult(base.ACCEPTED, http_status=status)
        if status == 401:
            return base.SendResult(base.FAILED, error_class=base.AUTH_FAILED,
                                   safe_message=message or 'Authentication failed (check ARKESEL_API_KEY).',
                                   http_status=status)
        if status == 402:
            return base.SendResult(base.FAILED, error_class=base.INSUFFICIENT_BALANCE,
                                   safe_message=message or 'Insufficient Arkesel SMS balance.', http_status=status)
        if status == 403:
            return base.SendResult(base.FAILED, error_class=base.GATEWAY_INACTIVE,
                                   safe_message=message or 'Inactive SMS gateway.', http_status=status)
        if status in (400, 422):
            lowered = message.lower()
            if 'sender' in lowered:
                error = base.INVALID_SENDER
            elif 'recipient' in lowered or 'phone' in lowered or 'number' in lowered:
                error = base.INVALID_RECIPIENT
            else:
                error = base.VALIDATION
            return base.SendResult(base.FAILED, error_class=error,
                                   safe_message=message or 'Validation error.', http_status=status)
        return base.SendResult(base.FAILED, error_class=base.UNEXPECTED_4XX,
                               safe_message=message or f'Unexpected HTTP {status}.', http_status=status)
