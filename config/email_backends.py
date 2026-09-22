"""Email over HTTPS for hosts that block outbound SMTP.

Render blocks outbound SMTP ports (25/465/587) on some plans: the SMTP backend
then times out and no reset email is ever delivered (staging 2026-09-22: 13 s,
nothing received). These backends deliver through the provider's HTTPS API.
Selected automatically when the provider's key is set (see settings.py).
"""
import base64
import logging
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


def _address(value):
    name, email = parseaddr(value)
    return name, email


def _html_body(message):
    for content, mimetype in getattr(message, 'alternatives', []) or []:
        if mimetype == 'text/html':
            return content
    return None


_HINTS = (
    ('unrecognised ip', 'Brevo blocks unknown IPs: Brevo > Security > Authorised IPs, deactivate blocking (Render IPs change).'),
    ('sender', 'The sender address is not verified in the provider (Brevo > Senders).'),
    ('not activated', 'The provider account is not activated for transactional email yet.'),
    ('unauthorized', 'The API key was rejected: check BREVO_API_KEY / RESEND_API_KEY.'),
    ('key not found', 'The API key was rejected: check BREVO_API_KEY / RESEND_API_KEY.'),
    ('domain', 'The sending domain is not verified with the provider.'),
)


def _provider_reason(response):
    """(provider error code, human hint) without echoing credentials."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = str(body.get('code') or body.get('name') or '')[:60] if isinstance(body, dict) else ''
    text = (str(body.get('message', '')) if isinstance(body, dict) else response.text or '').lower()
    for needle, hint in _HINTS:
        if needle in text or needle in code.lower():
            return code or 'error', hint
    return code or 'error', 'See the provider dashboard logs.'


class _HttpsEmailBackend(BaseEmailBackend):
    provider = ''

    def _post(self, message):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                response = self._post(message)
                if response.status_code >= 300:
                    code, hint = _provider_reason(response)
                    # Status, provider code and hint go in the message itself:
                    # Sentry scrubs extra fields whose text mentions a key.
                    logger.error(
                        'Email delivery failed: %s HTTP %s %s. %s',
                        self.provider, response.status_code, code, hint,
                        extra={'provider': self.provider, 'provider_status': response.status_code},
                    )
                    raise requests.HTTPError(f'{self.provider} HTTP {response.status_code}: {code}')
                sent += 1
            except requests.HTTPError:
                if not self.fail_silently:
                    raise
            except Exception as exc:
                logger.error('Email delivery failed: %s %s', self.provider, type(exc).__name__,
                             extra={'provider': self.provider})
                if not self.fail_silently:
                    raise
        return sent


class BrevoEmailBackend(_HttpsEmailBackend):
    """https://developers.brevo.com/reference/sendtransacemail"""
    provider = 'brevo'

    def _post(self, message):
        name, email = _address(message.from_email or settings.DEFAULT_FROM_EMAIL)
        payload = {
            'sender': {'email': email, **({'name': name} if name else {})},
            'to': [{'email': _address(r)[1]} for r in message.to],
            'subject': message.subject,
            'textContent': message.body,
        }
        if message.cc:
            payload['cc'] = [{'email': _address(r)[1]} for r in message.cc]
        if message.bcc:
            payload['bcc'] = [{'email': _address(r)[1]} for r in message.bcc]
        html = _html_body(message)
        if html:
            payload['htmlContent'] = html
        if message.attachments:
            payload['attachment'] = [
                {'name': filename, 'content': base64.b64encode(content if isinstance(content, bytes) else content.encode()).decode()}
                for filename, content, _mimetype in message.attachments if filename
            ]
        return requests.post(
            'https://api.brevo.com/v3/smtp/email', json=payload,
            headers={'api-key': settings.BREVO_API_KEY, 'accept': 'application/json'},
            timeout=settings.EMAIL_TIMEOUT,
        )


class ResendEmailBackend(_HttpsEmailBackend):
    """https://resend.com/docs/api-reference/emails/send-email"""
    provider = 'resend'

    def _post(self, message):
        payload = {
            'from': message.from_email or settings.DEFAULT_FROM_EMAIL,
            'to': list(message.to),
            'subject': message.subject,
            'text': message.body,
        }
        if message.cc:
            payload['cc'] = list(message.cc)
        if message.bcc:
            payload['bcc'] = list(message.bcc)
        html = _html_body(message)
        if html:
            payload['html'] = html
        return requests.post(
            'https://api.resend.com/emails', json=payload,
            headers={'Authorization': f'Bearer {settings.RESEND_API_KEY}'},
            timeout=settings.EMAIL_TIMEOUT,
        )
