"""Arkesel delivery callback, Django admin observability, configuration and phones."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from admin_notifications import conf
from admin_notifications.checks import ERROR, validate_configuration
from admin_notifications.models import AdminNotificationDelivery as Delivery
from admin_notifications.models import AdminNotificationProviderMessage as ProviderMessage
from admin_notifications.phone import AdminRecipientError, mask_recipient, normalize_admin_recipient
from admin_notifications.services.dispatcher import dispatch_due
from admin_notifications.tests.helpers import CALLBACK_SECRET, book, configure, make_world, patch_providers
from users.models import User

S = Delivery.Status


def _callback_url(token=CALLBACK_SECRET):
    return reverse('arkesel-sms-status', kwargs={'token': token})


@pytest.fixture
def sent_sms(settings, monkeypatch, db):
    configure(settings)
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    patch_providers(monkeypatch)
    world = make_world()
    assert book(world).status_code == 201
    dispatch_due()
    delivery = Delivery.objects.get(channel='SMS')
    assert delivery.status == S.SUBMITTED
    return delivery, delivery.provider_messages.get().provider_message_id


# --- callback -------------------------------------------------------------------

@pytest.mark.django_db
def test_callback_marks_delivered_and_is_idempotent(sent_sms):
    delivery, sms_id = sent_sms
    client = Client()
    for _ in range(3):
        response = client.get(_callback_url(), {'sms_id': sms_id, 'status': 'DELIVERED'})
        assert response.status_code == 200 and response.json() == {'ok': True}
    delivery.refresh_from_db()
    assert delivery.status == S.DELIVERED and delivery.delivered_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize('interim', ['SUBMITTED', 'QUEUED'])
def test_interim_statuses_are_recorded_without_final_state(sent_sms, interim):
    delivery, sms_id = sent_sms
    Client().post(_callback_url(), {'sms_id': sms_id, 'status': interim})
    delivery.refresh_from_db()
    assert delivery.status == S.SUBMITTED
    assert ProviderMessage.objects.get(provider_message_id=sms_id).provider_status == interim


@pytest.mark.django_db
@pytest.mark.parametrize('failure', ['NOT_DELIVERED', 'EXPIRED', 'PROHIBITED'])
def test_handset_failures_mark_undelivered_and_alert(sent_sms, failure):
    from unittest.mock import patch
    delivery, sms_id = sent_sms
    with patch('admin_notifications.services.metrics.alert') as alert:
        Client().get(_callback_url(), {'sms_id': sms_id, 'status': failure})
    delivery.refresh_from_db()
    assert delivery.status == S.UNDELIVERED and delivery.last_error_class == f'SMS_{failure}'
    assert alert.called


@pytest.mark.django_db
def test_late_interim_report_never_downgrades_a_final_one(sent_sms):
    delivery, sms_id = sent_sms
    client = Client()
    client.get(_callback_url(), {'sms_id': sms_id, 'status': 'DELIVERED'})
    client.get(_callback_url(), {'sms_id': sms_id, 'status': 'QUEUED'})
    client.get(_callback_url(), {'sms_id': sms_id, 'status': 'NOT_DELIVERED'})
    delivery.refresh_from_db()
    assert delivery.status == S.DELIVERED
    assert ProviderMessage.objects.get(provider_message_id=sms_id).provider_status == 'DELIVERED'


@pytest.mark.django_db
def test_unknown_id_and_invalid_status_change_nothing_and_leak_nothing(sent_sms):
    delivery, sms_id = sent_sms
    client = Client()
    for params in ({'sms_id': 'not-ours', 'status': 'DELIVERED'}, {'sms_id': sms_id, 'status': 'HACKED'},
                   {'sms_id': sms_id}, {}):
        response = client.get(_callback_url(), params)
        assert response.status_code == 200 and response.json() == {'ok': True}
    delivery.refresh_from_db()
    assert delivery.status == S.SUBMITTED


@pytest.mark.django_db
def test_wrong_or_unconfigured_secret_is_a_404(settings, sent_sms):
    _, sms_id = sent_sms
    client = Client()
    assert client.get(_callback_url('guess'), {'sms_id': sms_id, 'status': 'DELIVERED'}).status_code == 404
    settings.ARKESEL_SMS_CALLBACK_SECRET = ''
    assert client.get(_callback_url(), {'sms_id': sms_id, 'status': 'DELIVERED'}).status_code == 404


@pytest.mark.django_db
def test_callback_needs_no_login_or_csrf(sent_sms):
    delivery, sms_id = sent_sms
    client = Client(enforce_csrf_checks=True)
    assert client.post(_callback_url(), {'sms_id': sms_id, 'status': 'DELIVERED'}).status_code == 200


@pytest.mark.django_db
def test_multi_part_sms_delivered_only_when_every_part_is(settings, monkeypatch):
    configure(settings)
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    settings.ADMIN_SMS_MAX_CHARS_PER_MESSAGE = 300
    patch_providers(monkeypatch)
    assert book(make_world()).status_code == 201
    dispatch_due()
    delivery = Delivery.objects.get()
    ids = list(delivery.provider_messages.values_list('provider_message_id', flat=True))
    assert len(ids) >= 2
    client = Client()
    for sms_id in ids[:-1]:
        client.get(_callback_url(), {'sms_id': sms_id, 'status': 'DELIVERED'})
        delivery.refresh_from_db()
        assert delivery.status == S.SUBMITTED
    client.get(_callback_url(), {'sms_id': ids[-1], 'status': 'DELIVERED'})
    delivery.refresh_from_db()
    assert delivery.status == S.DELIVERED


# --- Django admin ---------------------------------------------------------------------

@pytest.fixture
def staff_client(db):
    admin_user = User.objects.create_superuser(email='ops-admin@example.com', phone='0209998877', password='StrongPass123!')
    client = Client()
    client.force_login(admin_user)
    return client


@pytest.mark.django_db
def test_admin_pages_mask_numbers_and_never_show_keys(settings, monkeypatch, staff_client):
    configure(settings)
    patch_providers(monkeypatch)
    assert book(make_world()).status_code == 201
    event_id = Delivery.objects.first().event_id
    pages = [
        reverse('admin:admin_notifications_adminnotificationdelivery_changelist'),
        reverse('admin:admin_notifications_adminnotificationevent_changelist'),
        reverse('admin:admin_notifications_adminnotificationevent_change', args=[event_id]),
        reverse('admin:admin_notifications_adminnotificationdelivery_change', args=[Delivery.objects.first().pk]),
    ]
    for url in pages:
        response = staff_client.get(url)
        assert response.status_code == 200, url
        html = response.content.decode()
        for secret in ('ark-test-key-DO-NOT-LEAK', 'wa-token-DO-NOT-LEAK', CALLBACK_SECRET,
                       '233551057139', '233200031713', '233541604786'):
            assert secret not in html
    assert '233 55 *** 7139' in staff_client.get(pages[0]).content.decode()
    assert 'SIMAME NEW ORDER' in staff_client.get(pages[2]).content.decode()  # rendered preview


@pytest.mark.django_db
def test_admin_retry_action(settings, monkeypatch, staff_client):
    from admin_notifications.providers import base
    from admin_notifications.tests.helpers import FakeSms
    configure(settings)
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    patch_providers(monkeypatch, sms=FakeSms(script=lambda r, m, n: base.SendResult(
        base.FAILED, error_class=base.AUTH_FAILED)))
    from unittest.mock import patch
    assert book(make_world()).status_code == 201
    with patch('admin_notifications.services.metrics.alert'):
        dispatch_due()
    delivery = Delivery.objects.get()
    assert delivery.status == S.FAILED
    response = staff_client.post(
        reverse('admin:admin_notifications_adminnotificationdelivery_changelist'),
        {'action': 'retry_selected', '_selected_action': [str(delivery.pk)]},
    )
    assert response.status_code == 302
    delivery.refresh_from_db()
    assert delivery.status == S.PENDING and delivery.attempt_count == 0


@pytest.mark.django_db
def test_outbox_rows_cannot_be_edited_or_deleted_in_admin(settings, monkeypatch, staff_client):
    configure(settings)
    assert book(make_world()).status_code == 201
    delivery = Delivery.objects.first()
    url = reverse('admin:admin_notifications_adminnotificationdelivery_change', args=[delivery.pk])
    staff_client.post(url, {'status': 'DELIVERED'})
    delivery.refresh_from_db()
    assert delivery.status == S.PENDING
    delete = reverse('admin:admin_notifications_adminnotificationdelivery_delete', args=[delivery.pk])
    assert staff_client.get(delete).status_code == 403


# --- configuration / phones ---------------------------------------------------------

@pytest.mark.parametrize('raw', ['0551057139', '+233551057139', '233551057139', '055 105 7139',
                                 '+233 55 105 7139', '055-105-7139'])
def test_admin_recipient_forms_normalize(raw):
    assert normalize_admin_recipient(raw) == '233551057139'


@pytest.mark.parametrize('raw', ['', '12345', '+447911123456', '0551057', 'abc', '+2330551057139x'])
def test_malformed_or_foreign_recipients_rejected(raw):
    with pytest.raises(AdminRecipientError):
        normalize_admin_recipient(raw)


def test_recipient_list_dedupes_and_separates_invalid():
    parsed = conf.parse_recipients('0551057139, +233551057139,0200031713, bad, +447911123456')
    assert parsed.valid == ('233551057139', '233200031713')
    assert parsed.invalid == ('bad', '+447911123456')
    assert mask_recipient('233551057139') == '233 55 *** 7139'


def test_everything_defaults_off(settings):
    for name in ('ADMIN_ORDER_NOTIFICATIONS_ENABLED', 'ADMIN_SMS_NOTIFICATIONS_ENABLED',
                 'ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED'):
        delattr(settings, name)
    assert not conf.notifications_enabled() and not conf.sms_enabled() and not conf.whatsapp_enabled()


def test_sandbox_defaults_on_outside_production(settings):
    settings.ARKESEL_SMS_SANDBOX = ''
    settings.ADMIN_NOTIFICATION_ENVIRONMENT = 'staging'
    assert conf.arkesel_sms_sandbox() is True
    settings.ADMIN_NOTIFICATION_ENVIRONMENT = 'production'
    assert conf.arkesel_sms_sandbox() is False


def test_config_report_flags_problems_without_printing_secrets(settings):
    configure(settings)
    settings.ADMIN_SMS_RECIPIENTS = '0551057139,12345'
    settings.ARKESEL_SMS_SANDBOX = 'true'
    settings.ARKESEL_WHATSAPP_ORDER_UPDATE_TEMPLATE_ID = ''
    report = validate_configuration()
    errors = [m for level, m in report if level == ERROR]
    assert any('invalid Ghana number' in m for m in errors)
    assert any('SANDBOX is on in production' in m for m in errors)
    assert any('ARKESEL_WHATSAPP_ORDER_UPDATE_TEMPLATE_ID' in m for m in errors)
    text = repr(report)
    for secret in ('ark-test-key-DO-NOT-LEAK', 'wa-token-DO-NOT-LEAK', CALLBACK_SECRET, '12345'):
        assert secret not in text
    assert 'sha256:' in text


@pytest.mark.django_db
def test_check_command_passes_for_valid_production_config(settings):
    configure(settings)
    out = StringIO()
    call_command('check_admin_notifications', stdout=out)
    text = out.getvalue()
    assert '[ERROR]' not in text and 'WhatsApp recipients: 3' in text and 'SMS recipients: 1' in text
    assert 'ark-test-key-DO-NOT-LEAK' not in text


@pytest.mark.django_db
def test_check_command_exits_nonzero_on_errors(settings):
    configure(settings)
    settings.ARKESEL_API_KEY = ''
    with pytest.raises(SystemExit):
        call_command('check_admin_notifications', stdout=StringIO())


def test_system_check_warns_but_never_blocks(settings):
    from admin_notifications.checks import admin_notification_config_check
    configure(settings)
    settings.ARKESEL_API_KEY = ''
    messages = admin_notification_config_check()
    assert messages and all(m.level < 40 for m in messages)  # WARNING, not ERROR


@pytest.mark.django_db
def test_report_command(settings, monkeypatch):
    configure(settings)
    patch_providers(monkeypatch)
    assert book(make_world()).status_code == 201
    dispatch_due()
    out = StringIO()
    call_command('admin_notification_report', stdout=out)
    text = out.getvalue()
    assert 'Orders notified (NEW_ORDER): 1  COD: 1  Online: 0' in text
    assert 'WHATSAPP: 3 deliveries, success 100.0%' in text
    assert 'SMS: 1 deliveries, success 100.0%' in text


@pytest.mark.django_db
def test_callback_only_touches_the_matching_delivery(settings, monkeypatch):
    configure(settings)
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    patch_providers(monkeypatch)
    world = make_world()
    assert book(world).status_code == 201 and book(world).status_code == 201
    dispatch_due()
    first, second = Delivery.objects.order_by('created_at')
    Client().get(_callback_url(), {'sms_id': first.provider_messages.get().provider_message_id,
                                   'status': 'DELIVERED'})
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == S.DELIVERED and second.status == S.SUBMITTED
    assert first.event.order_id != second.event.order_id


@pytest.mark.django_db
@pytest.mark.parametrize('params', [
    {'sms_id': 'x' * 5000, 'status': 'DELIVERED'},
    {'sms_id': "' OR 1=1 --", 'status': 'DELIVERED'},
    {'sms_id': '\u0000‮', 'status': 'DELIVERED'},
    {'sms_id': ['a', 'b'], 'status': ['DELIVERED', 'EXPIRED']},
    {'status': 'delivered'},
])
def test_malformed_callbacks_are_harmless(sent_sms, params):
    delivery, _ = sent_sms
    response = Client().get(_callback_url(), params)
    assert response.status_code == 200 and response.json() == {'ok': True}
    delivery.refresh_from_db()
    assert delivery.status == S.SUBMITTED


@pytest.mark.django_db
def test_callback_replay_after_redelivery_request_is_ignored(sent_sms):
    delivery, sms_id = sent_sms
    client = Client()
    client.get(_callback_url(), {'sms_id': sms_id, 'status': 'NOT_DELIVERED'})
    for _ in range(3):  # replayed reports, including a stale "DELIVERED"
        client.get(_callback_url(), {'sms_id': sms_id, 'status': 'DELIVERED'})
    delivery.refresh_from_db()
    assert delivery.status == S.UNDELIVERED
