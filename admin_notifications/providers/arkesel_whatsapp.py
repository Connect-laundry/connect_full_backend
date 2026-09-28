"""ArkeselWhatsAppProvider: official WhatsApp Business API through Arkesel KOVA IQ.

Contract (official spec developers.arkesel.com, api_spec v2.4.0,
operation ``send_whatsapp_template_message``):

    POST https://kova-api.arkesel.com/ext-api/v1/whatsapp-business/{organization_id}/send-template-message
    header  X-API-Token: <token>
    body    {"to": "233...", "inbox_id": "<inbox afr_uuid>", "template_id": "<template afr_uuid or provider id>",
             "components": [{"type": "body", "parameters": [{"type": "text", "text": "..."}]}]}
    200     {"status": "success", "data": {"message_id": "...", "status": "sent", ...}}
    401 unauthorized, 422 validation error, 500 internal error

The API reports acceptance ("sent"); no delivery webhook is documented for
one-off template messages, so WhatsApp deliveries end at SENT.
"""
from __future__ import annotations

from .. import conf
from . import base
from .arkesel_sms import transport_result
from .http import post_json

PROVIDER = 'arkesel_whatsapp'


class ArkeselWhatsAppProvider:
    name = PROVIDER

    def missing_config(self, template_key: str | None = None) -> list[str]:
        missing = []
        if not conf.arkesel_whatsapp_token():
            missing.append('ARKESEL_WHATSAPP_API_TOKEN')
        if not conf.arkesel_whatsapp_org_id():
            missing.append('ARKESEL_WHATSAPP_ORGANIZATION_ID')
        if not conf.arkesel_whatsapp_inbox_id():
            missing.append('ARKESEL_WHATSAPP_INBOX_ID')
        keys = [template_key] if template_key else ['new_order', 'order_update']
        for key in keys:
            if not conf.whatsapp_template_id(key):
                missing.append(
                    'ARKESEL_WHATSAPP_NEW_ORDER_TEMPLATE_ID' if key == 'new_order'
                    else 'ARKESEL_WHATSAPP_ORDER_UPDATE_TEMPLATE_ID'
                )
        return missing

    def build_request(self, recipient: str, template_key: str, params) -> tuple[str, dict, dict]:
        from urllib.parse import quote
        url = (
            f'{conf.arkesel_whatsapp_base_url()}/ext-api/v1/whatsapp-business/'
            f'{quote(conf.arkesel_whatsapp_org_id(), safe="")}/send-template-message'
        )
        payload = {
            'to': recipient,
            'inbox_id': conf.arkesel_whatsapp_inbox_id(),
            'template_id': conf.whatsapp_template_id(template_key),
            'components': [{
                'type': 'body',
                'parameters': [{'type': 'text', 'text': str(value)} for value in params],
            }],
        }
        return url, {'X-API-Token': conf.arkesel_whatsapp_token()}, payload

    def send(self, recipient: str, template_key: str, params) -> base.SendResult:
        missing = self.missing_config(template_key)
        if missing:
            return base.SendResult(base.FAILED, error_class=base.CONFIG_MISSING,
                                   safe_message='Missing: ' + ', '.join(missing))
        url, headers, payload = self.build_request(recipient, template_key, params)
        outcome = post_json(url, headers=headers, payload=payload, timeout=conf.http_timeouts())
        return self.interpret(outcome)

    @staticmethod
    def interpret(outcome) -> base.SendResult:
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
            data = body.get('data') if isinstance(body.get('data'), dict) else {}
            message_id = str(data.get('message_id') or '')[:128]
            return base.SendResult(base.ACCEPTED, provider_message_id=message_id, http_status=status)
        if status == 401:
            return base.SendResult(base.FAILED, error_class=base.AUTH_FAILED,
                                   safe_message=message or 'Unauthorized (check ARKESEL_WHATSAPP_API_TOKEN).',
                                   http_status=status)
        if status == 404:
            return base.SendResult(base.FAILED, error_class=base.NOT_FOUND,
                                   safe_message=message or 'Organization, inbox or endpoint not found.',
                                   http_status=status)
        if status in (400, 422):
            lowered = message.lower()
            if 'template' in lowered:
                error = base.TEMPLATE_INVALID
            elif ' to ' in f' {lowered} ' or 'recipient' in lowered or 'phone' in lowered:
                error = base.INVALID_RECIPIENT
            else:
                error = base.VALIDATION
            return base.SendResult(base.FAILED, error_class=error,
                                   safe_message=message or 'Validation error.', http_status=status)
        if status == 402:
            return base.SendResult(base.FAILED, error_class=base.INSUFFICIENT_BALANCE,
                                   safe_message=message or 'Insufficient WhatsApp balance.', http_status=status)
        return base.SendResult(base.FAILED, error_class=base.UNEXPECTED_4XX,
                               safe_message=message or f'Unexpected HTTP {status}.', http_status=status)
