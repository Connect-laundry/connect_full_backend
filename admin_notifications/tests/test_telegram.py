"""Telegram channel: Bot API contract (mocked HTTP) and end-to-end outbox flow."""
import io
import json
from io import StringIO
from unittest.mock import patch

import pytest
import requests
from django.core.management import call_command

from admin_notifications.models import AdminNotificationDelivery as Delivery
from admin_notifications.phone import mask_recipient
from admin_notifications.providers import base
from admin_notifications.providers.telegram import TelegramProvider
from admin_notifications.rendering import render_telegram_parts
from admin_notifications.services.dispatcher import dispatch_due
from admin_notifications.tests.helpers import book, configure, make_world

TOKEN = '123456789:AAFakeTokenForTestsOnly_DO_NOT_LEAK'
CHAT = '-1001234567890'


class FakeResponse:
    def __init__(self, status, body=None, raw=None):
        self.status_code = status
        data = raw if raw is not None else json.dumps(body).encode()
        buffer = io.BytesIO(data)
        self.raw = type('Raw', (), {'read': lambda _self, n, decode_content=True: buffer.read(n)})()

    def close(self):
        pass


def _telegram(settings, chats=CHAT):
    configure(settings, sms=False, whatsapp=False)
    settings.ADMIN_TELEGRAM_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_TELEGRAM_CHAT_IDS = chats
    settings.TELEGRAM_BOT_TOKEN = TOKEN


def _post(responses):
    calls = []
    seq = iter(responses)

    def fake(url, headers, data, timeout, verify, stream, allow_redirects):
        calls.append({'url': url, 'json': json.loads(data), 'verify': verify})
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item
    return patch('admin_notifications.providers.http.requests.post', side_effect=fake), calls


def _ok(message_id=7):
    return FakeResponse(200, {'ok': True, 'result': {'message_id': message_id, 'chat': {'id': int(CHAT)}}})


# --- provider contract -----------------------------------------------------------------

def test_send_message_request_shape(settings):
    _telegram(settings)
    ctx, calls = _post([_ok(42)])
    with ctx:
        result = TelegramProvider().send(CHAT, 'SIMAME NEW ORDER CN-1\nGH₵ 20.00 é')
    assert result.accepted and result.provider_message_id == f'{CHAT}:42'
    call = calls[0]
    assert call['url'] == f'https://api.telegram.org/bot{TOKEN}/sendMessage'
    assert call['json'] == {'chat_id': CHAT, 'text': 'SIMAME NEW ORDER CN-1\nGH₵ 20.00 é',
                            'disable_web_page_preview': True}
    assert 'parse_mode' not in call['json'] and call['verify'] is True


@pytest.mark.parametrize('status,body,outcome,error', [
    (401, {'ok': False, 'error_code': 401, 'description': 'Unauthorized'}, base.FAILED, base.AUTH_FAILED),
    (404, {'ok': False, 'error_code': 404, 'description': 'Not Found'}, base.FAILED, base.AUTH_FAILED),
    (400, {'ok': False, 'error_code': 400, 'description': 'Bad Request: chat not found'}, base.FAILED,
     base.INVALID_RECIPIENT),
    (403, {'ok': False, 'error_code': 403, 'description': 'Forbidden: bot was kicked from the group chat'},
     base.FAILED, base.INVALID_RECIPIENT),
    (400, {'ok': False, 'error_code': 400, 'description': 'Bad Request: message is too long'}, base.FAILED,
     base.VALIDATION),
    (429, {'ok': False, 'error_code': 429, 'description': 'Too Many Requests: retry after 5',
           'parameters': {'retry_after': 5}}, base.RETRYABLE, base.RATE_LIMITED),
    (502, {'ok': False, 'error_code': 502, 'description': 'Bad Gateway'}, base.RETRYABLE, base.PROVIDER_5XX),
])
def test_error_classes(settings, status, body, outcome, error):
    _telegram(settings)
    ctx, _ = _post([FakeResponse(status, body)])
    with ctx:
        result = TelegramProvider().send(CHAT, 'x')
    assert (result.outcome, result.error_class) == (outcome, error)


def test_group_upgraded_to_supergroup_names_the_new_id(settings):
    _telegram(settings)
    ctx, _ = _post([FakeResponse(400, {'ok': False, 'error_code': 400,
                                       'description': 'Bad Request: group chat was upgraded to a supergroup chat',
                                       'parameters': {'migrate_to_chat_id': -1009999999999}})])
    with ctx:
        result = TelegramProvider().send(CHAT, 'x')
    assert result.error_class == base.INVALID_RECIPIENT and '-1009999999999' in result.safe_message


def test_timeouts_and_network(settings):
    _telegram(settings)
    ctx, _ = _post([requests.exceptions.ReadTimeout('slow'), requests.exceptions.ConnectTimeout('down')])
    with ctx:
        assert TelegramProvider().send(CHAT, 'x').outcome == base.UNKNOWN
        assert TelegramProvider().send(CHAT, 'x').outcome == base.RETRYABLE


def test_missing_token_never_calls_telegram(settings):
    _telegram(settings)
    settings.TELEGRAM_BOT_TOKEN = ''
    ctx, calls = _post([])
    with ctx:
        result = TelegramProvider().send(CHAT, 'x')
    assert calls == [] and result.error_class == base.CONFIG_MISSING


def test_token_never_appears_in_results_or_errors(settings):
    _telegram(settings)
    ctx, _ = _post([requests.exceptions.ConnectionError(f'https://api.telegram.org/bot{TOKEN}/sendMessage failed')])
    with ctx:
        result = TelegramProvider().send(CHAT, 'x')
    assert TOKEN not in repr(result)


def test_chat_id_parsing_and_masking(settings):
    from admin_notifications import conf
    parsed = conf.parse_telegram_chat_ids('-1001234567890, 55123456, -1001234567890, @simame_ops, abc')
    assert parsed.valid == ('-1001234567890', '55123456')
    assert parsed.invalid == ('@simame_ops', 'abc')
    assert mask_recipient('-1001234567890') == 'chat ...7890'
    assert mask_recipient('233551057139') == '233 55 *** 7139'


# --- end to end through the outbox ----------------------------------------------------------

@pytest.mark.django_db
def test_booking_reaches_the_telegram_group_once(settings):
    _telegram(settings)
    response = book(make_world(first_name='Kwame', last_name='Owusu'))
    assert response.status_code == 201
    rows = Delivery.objects.all()
    assert [(r.channel, r.recipient, r.provider) for r in rows] == [('TELEGRAM', CHAT, 'telegram')]
    ctx, calls = _post([_ok(101)])
    with ctx:
        dispatch_due()
        dispatch_due()  # nothing more to send
    assert len(calls) == 1
    text = calls[0]['json']['text']
    assert 'SIMAME NEW ORDER' in text and 'Kwame Owusu' in text and 'CASH ON DELIVERY' in text
    assert 'Amount to collect: GHS' in text and 'google.com/maps' in text
    delivery = Delivery.objects.get()
    assert delivery.status == Delivery.Status.SENT and delivery.provider_message_id == f'{CHAT}:101'


@pytest.mark.django_db
def test_telegram_is_independent_of_other_channels(settings, monkeypatch):
    from admin_notifications.tests.helpers import FakeSms, patch_providers
    _telegram(settings)
    settings.ADMIN_SMS_NOTIFICATIONS_ENABLED = True
    patch_providers(monkeypatch, sms=FakeSms(script=lambda r, m, n: base.SendResult(
        base.FAILED, error_class=base.INSUFFICIENT_BALANCE)))
    assert book(make_world()).status_code == 201
    ctx, _ = _post([_ok(5)])
    with ctx, patch('admin_notifications.services.metrics.alert'):
        dispatch_due()
    statuses = dict(Delivery.objects.values_list('channel', 'status'))
    assert statuses == {'SMS': 'FAILED', 'TELEGRAM': 'SENT'}


@pytest.mark.django_db
def test_long_order_splits_for_telegram_at_line_boundaries(settings):
    _telegram(settings)
    from admin_notifications.models import AdminNotificationEvent
    import copy
    order_id = book(make_world()).data['id']
    payload = copy.deepcopy(AdminNotificationEvent.objects.get(order_id=order_id).payload)
    payload['items'] = [{'name': f'Agbada set {i} with embroidered cap', 'quantity': 2, 'unit_price': '35.00',
                         'line_total': '70.00'} for i in range(120)]
    parts = render_telegram_parts(payload)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= 4096
    for i in range(120):
        line = f'2 x Agbada set {i} with embroidered cap @ GHS 35.00 = GHS 70.00'
        assert sum(line in part for part in parts) == 1


@pytest.mark.django_db
def test_telegram_keeps_unicode_that_sms_folds(settings):
    _telegram(settings)
    order_id = book(make_world(first_name='Akosua', last_name='Ɔpare')).data['id']
    from admin_notifications.models import AdminNotificationEvent
    payload = AdminNotificationEvent.objects.get(order_id=order_id).payload
    payload['notes'] = 'Call first \U0001F4DE'
    (text,) = render_telegram_parts(payload)
    assert 'Ɔpare' in text and '\U0001F4DE' in text


def test_config_check_reports_telegram(settings):
    from admin_notifications.checks import ERROR, validate_configuration
    _telegram(settings, chats='-1001234567890,@bad')
    report = validate_configuration()
    assert any('Telegram chats: 1' in m for _, m in report)
    assert any(level == ERROR and 'ADMIN_TELEGRAM_CHAT_IDS' in m for level, m in report)
    assert TOKEN not in repr(report)


@pytest.mark.django_db
def test_find_chats_command_lists_groups_without_printing_token(settings):
    _telegram(settings)

    class R:
        status_code = 200

        def json(self):
            return {'ok': True, 'result': [
                {'update_id': 1, 'my_chat_member': {'chat': {'id': -1001234567890, 'type': 'supergroup',
                                                             'title': 'Simame Ops'}}},
                {'update_id': 2, 'message': {'chat': {'id': 555, 'type': 'private', 'first_name': 'Kofi'}}},
            ]}
    out = StringIO()
    with patch('admin_notifications.management.commands.telegram_find_chats.requests.get', return_value=R()) as get:
        call_command('telegram_find_chats', stdout=out)
    text = out.getvalue()
    assert '-1001234567890\tsupergroup\tSimame Ops' in text and '555\tprivate\tKofi' in text
    assert TOKEN not in text
    assert get.call_args.args[0].endswith('/getUpdates')
