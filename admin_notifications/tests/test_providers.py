"""Arkesel SMS v2 and Arkesel WhatsApp (KOVA) contracts, mocked at the HTTP layer."""
import io
import json
from unittest.mock import patch

import pytest
import requests
from urllib3.exceptions import NameResolutionError

from admin_notifications.providers import base
from admin_notifications.providers.arkesel_sms import ArkeselSmsProvider
from admin_notifications.providers.arkesel_whatsapp import ArkeselWhatsAppProvider
from admin_notifications.tests.helpers import CALLBACK_SECRET, configure

RECIPIENT = '233551057139'


class FakeResponse:
    def __init__(self, status, body=None, raw_bytes=None):
        self.status_code = status
        data = raw_bytes if raw_bytes is not None else (json.dumps(body).encode() if body is not None else b'')
        self.raw = io.BytesIO(data)
        self.raw.read = (lambda original: (lambda n, decode_content=True: original(n)))(self.raw.read)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _config(settings):
    configure(settings)


def _post(response=None, exc=None):
    calls = []

    def fake(url, headers, data, timeout, verify, stream, allow_redirects):
        calls.append({'url': url, 'headers': headers, 'json': json.loads(data), 'timeout': timeout,
                      'verify': verify, 'allow_redirects': allow_redirects})
        if exc:
            raise exc
        return response
    return patch('admin_notifications.providers.http.requests.post', side_effect=fake), calls


# --- SMS --------------------------------------------------------------------------

def test_sms_request_matches_arkesel_v2_contract():
    ctx, calls = _post(FakeResponse(200, {'status': 'success', 'data': [{'recipient': RECIPIENT, 'id': 'sms-1'}]}))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'Hello ops')
    assert result.accepted and result.provider_message_id == 'sms-1'
    call = calls[0]
    assert call['url'] == 'https://sms.arkesel.com/api/v2/sms/send'
    assert call['headers']['api-key'] == 'ark-test-key-DO-NOT-LEAK'
    assert call['json']['sender'] == 'SIMAME'
    assert call['json']['recipients'] == [RECIPIENT]
    assert call['json']['message'] == 'Hello ops'
    assert call['json']['callback_url'] == (
        f'https://api.simame-ops.com/api/v1/integrations/arkesel/sms/status/{CALLBACK_SECRET}/')
    assert 'sandbox' not in call['json']  # production
    assert call['verify'] is True and call['allow_redirects'] is False
    assert call['timeout'] == (5.0, 15.0)


def test_sms_sandbox_flag_sent_outside_production(settings):
    settings.ADMIN_NOTIFICATION_ENVIRONMENT = 'staging'
    settings.ARKESEL_SMS_SANDBOX = ''
    ctx, calls = _post(FakeResponse(200, {'status': 'success', 'data': [{'recipient': RECIPIENT, 'id': 'x'}]}))
    with ctx:
        ArkeselSmsProvider().send(RECIPIENT, 'hi')
    assert calls[0]['json']['sandbox'] is True


@pytest.mark.parametrize('status,body,outcome,error', [
    (401, {'status': 'error', 'message': 'Invalid API key'}, base.FAILED, base.AUTH_FAILED),
    (402, {'status': 'error', 'message': 'Insufficient balance or invalid coverage!'}, base.FAILED,
     base.INSUFFICIENT_BALANCE),
    (403, {'status': 'error', 'message': 'Inactive SMS Gateway!'}, base.FAILED, base.GATEWAY_INACTIVE),
    (422, {'status': 'error', 'message': 'The message field is required.'}, base.FAILED, base.VALIDATION),
    (422, {'status': 'error', 'message': 'Invalid sender id'}, base.FAILED, base.INVALID_SENDER),
    (500, {'status': 'error', 'message': 'SMS request failed!'}, base.RETRYABLE, base.PROVIDER_5XX),
    (503, None, base.RETRYABLE, base.PROVIDER_5XX),
    (429, None, base.RETRYABLE, base.RATE_LIMITED),
    (200, {'status': 'error', 'message': 'nope'}, base.FAILED, base.PROVIDER_REJECTED),
])
def test_sms_status_classes(status, body, outcome, error):
    ctx, _ = _post(FakeResponse(status, body))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert (result.outcome, result.error_class) == (outcome, error)


def test_sms_malformed_json_on_200_is_unknown_not_success():
    ctx, _ = _post(FakeResponse(200, raw_bytes=b'<html>gateway</html>'))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert result.outcome == base.UNKNOWN and result.error_class == base.MALFORMED_RESPONSE


def test_sms_invalid_number_reported_in_success_body():
    ctx, _ = _post(FakeResponse(200, {'status': 'success', 'data': [{'invalid numbers': [RECIPIENT]}]}))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert result.outcome == base.FAILED and result.error_class == base.INVALID_RECIPIENT


def test_sms_read_timeout_is_ambiguous():
    ctx, _ = _post(exc=requests.exceptions.ReadTimeout('read timed out'))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert result.outcome == base.UNKNOWN and result.error_class == base.TIMEOUT_AFTER_SEND


@pytest.mark.parametrize('exc', [
    requests.exceptions.ConnectTimeout('connect timed out'),
    requests.exceptions.ConnectionError(NameResolutionError('sms.arkesel.com', None, 'dns failure')),
    requests.exceptions.SSLError('handshake'),
])
def test_sms_failures_before_sending_are_retryable(exc):
    ctx, _ = _post(exc=exc)
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert result.outcome == base.RETRYABLE and result.error_class == base.NETWORK


def test_sms_connection_reset_after_send_is_unknown():
    ctx, _ = _post(exc=requests.exceptions.ConnectionError('Connection aborted. RemoteDisconnected'))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert result.outcome == base.UNKNOWN


def test_sms_oversized_response_is_not_trusted():
    ctx, _ = _post(FakeResponse(200, raw_bytes=b'{' + b' ' * (70 * 1024) + b'}'))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert result.outcome == base.UNKNOWN


def test_sms_missing_key_fails_without_calling_provider(settings):
    settings.ARKESEL_API_KEY = ''
    ctx, calls = _post(FakeResponse(200, {}))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert calls == [] and result.error_class == base.CONFIG_MISSING and not result.accepted


def test_provider_error_text_masks_phone_numbers():
    ctx, _ = _post(FakeResponse(422, {'status': 'error', 'message': 'Recipient 233551057139 is invalid'}))
    with ctx:
        result = ArkeselSmsProvider().send(RECIPIENT, 'x')
    assert '233551057139' not in result.safe_message and '233***' in result.safe_message


# --- WhatsApp ---------------------------------------------------------------------------

def test_whatsapp_request_matches_kova_contract():
    ctx, calls = _post(FakeResponse(200, {'status': 'success', 'message': 'Template message sent successfully',
                                          'data': {'message_id': 'msg-123', 'status': 'sent'}}))
    with ctx:
        result = ArkeselWhatsAppProvider().send(RECIPIENT, 'new_order', ['CN-1', 'Sat 27 Sep'])
    assert result.accepted and result.provider_message_id == 'msg-123'
    call = calls[0]
    assert call['url'] == ('https://kova-api.arkesel.com/ext-api/v1/whatsapp-business/org_simame/'
                           'send-template-message')
    assert call['headers']['X-API-Token'] == 'wa-token-DO-NOT-LEAK'
    assert call['json'] == {
        'to': RECIPIENT, 'inbox_id': 'inbox-uuid-1', 'template_id': 'tmpl-new-order',
        'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'CN-1'},
                                                       {'type': 'text', 'text': 'Sat 27 Sep'}]}],
    }


def test_whatsapp_update_template_id_used_for_updates():
    ctx, calls = _post(FakeResponse(200, {'status': 'success', 'data': {'message_id': 'm'}}))
    with ctx:
        ArkeselWhatsAppProvider().send(RECIPIENT, 'order_update', ['a', 'b', 'c', 'd'])
    assert calls[0]['json']['template_id'] == 'tmpl-order-update'


@pytest.mark.parametrize('status,body,outcome,error', [
    (401, {'status': 'failed', 'message': 'Unauthorized', 'code': 401}, base.FAILED, base.AUTH_FAILED),
    (422, {'status': 'failed', 'message': 'Template not approved', 'code': 422}, base.FAILED,
     base.TEMPLATE_INVALID),
    (422, {'status': 'failed', 'message': 'Validation failed', 'code': 422}, base.FAILED, base.VALIDATION),
    (404, {'status': 'failed', 'message': 'Organization not found'}, base.FAILED, base.NOT_FOUND),
    (500, {'status': 'failed', 'message': 'Internal Server Error'}, base.RETRYABLE, base.PROVIDER_5XX),
])
def test_whatsapp_status_classes(status, body, outcome, error):
    ctx, _ = _post(FakeResponse(status, body))
    with ctx:
        result = ArkeselWhatsAppProvider().send(RECIPIENT, 'new_order', ['x'])
    assert (result.outcome, result.error_class) == (outcome, error)


def test_whatsapp_timeout_is_unknown():
    ctx, _ = _post(exc=requests.exceptions.ReadTimeout('slow'))
    with ctx:
        assert ArkeselWhatsAppProvider().send(RECIPIENT, 'new_order', ['x']).outcome == base.UNKNOWN


def test_whatsapp_not_configured_never_fakes_success(settings):
    settings.ARKESEL_WHATSAPP_API_TOKEN = ''
    settings.ARKESEL_WHATSAPP_NEW_ORDER_TEMPLATE_ID = ''
    ctx, calls = _post(FakeResponse(200, {'status': 'success'}))
    with ctx:
        result = ArkeselWhatsAppProvider().send(RECIPIENT, 'new_order', ['x'])
    assert calls == []
    assert result.outcome == base.FAILED and result.error_class == base.CONFIG_MISSING
    assert 'ARKESEL_WHATSAPP_API_TOKEN' in result.safe_message
    assert 'ARKESEL_WHATSAPP_NEW_ORDER_TEMPLATE_ID' in result.safe_message
