"""Dispatcher behaviour: fan-out, isolation of failures, retry/backoff, ambiguity,
expiry, ordering, flags, and the failure drills."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from admin_notifications.models import AdminNotificationDelivery as Delivery
from admin_notifications.models import AdminNotificationEvent as Event
from admin_notifications.providers import base
from admin_notifications.services.dispatcher import dispatch_due, requeue
from admin_notifications.tests.helpers import (
    SMS_NORMALIZED, WA_NORMALIZED, FakeSms, FakeWhatsApp, book, client_for, configure, make_world,
    patch_providers,
)
from ordering.models import Order

pytestmark = pytest.mark.django_db
S = Delivery.Status


@pytest.fixture
def world(settings):
    configure(settings)
    return make_world()


@pytest.fixture
def alerts():
    with patch('admin_notifications.services.metrics.alert') as mocked:
        yield mocked


def _order(world, **kw):
    response = book(world, **kw)
    assert response.status_code == 201
    return Order.objects.get(pk=response.data['id'])


def _fail(error, outcome=base.FAILED):
    return base.SendResult(outcome, error_class=error, safe_message=error)


def test_new_order_reaches_three_whatsapp_admins_and_one_sms_admin_exactly_once(monkeypatch, world):
    providers = patch_providers(monkeypatch)
    order = _order(world)
    result = dispatch_due()
    assert result.accepted == 4
    assert sorted(r for r, _, _ in providers.whatsapp.calls) == sorted(WA_NORMALIZED)
    assert [r for r, _ in providers.sms.calls] == [SMS_NORMALIZED]
    assert all(key == 'new_order' for _, key, _ in providers.whatsapp.calls)
    assert order.order_no in providers.sms.calls[0][1]
    statuses = dict(Delivery.objects.values_list('channel', 'status').distinct())
    assert statuses == {'WHATSAPP': S.SENT, 'SMS': S.SUBMITTED}
    assert Event.objects.get().processed_at is not None
    # Re-running (cron + kick + sweep overlap) sends nothing more.
    dispatch_due()
    dispatch_due()
    assert len(providers.whatsapp.calls) == 3 and len(providers.sms.calls) == 1


def test_one_whatsapp_recipient_failing_does_not_stop_the_others(monkeypatch, world, alerts):
    bad = WA_NORMALIZED[1]
    wa = FakeWhatsApp(script=lambda r, k, p, n: _fail(base.INVALID_RECIPIENT) if r == bad
                      else base.SendResult(base.ACCEPTED, provider_message_id=f'wa-{n}'))
    patch_providers(monkeypatch, whatsapp=wa)
    _order(world)
    dispatch_due()
    by_recipient = dict(Delivery.objects.filter(channel='WHATSAPP').values_list('recipient', 'status'))
    assert by_recipient.pop(bad) == S.FAILED
    assert set(by_recipient.values()) == {S.SENT}
    assert Delivery.objects.get(channel='SMS').status == S.SUBMITTED


def test_retryable_errors_back_off_then_go_dead(monkeypatch, settings, world, alerts):
    settings.ADMIN_NOTIFICATION_MAX_ATTEMPTS = 3
    settings.ADMIN_NOTIFICATION_RETRY_SCHEDULE_SECONDS = '30,120'
    sms = FakeSms(script=lambda r, m, n: _fail(base.PROVIDER_5XX, base.RETRYABLE))
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    patch_providers(monkeypatch, sms=sms)
    _order(world)
    start = timezone.now()
    dispatch_due()
    delivery = Delivery.objects.get()
    assert delivery.status == S.RETRY and delivery.attempt_count == 1
    assert 25 <= (delivery.next_attempt_at - start).total_seconds() <= 40
    dispatch_due()  # not due yet
    assert len(sms.calls) == 1
    for expected_delay in (120,):
        Delivery.objects.update(next_attempt_at=timezone.now())
        before = timezone.now()
        dispatch_due()
        delivery.refresh_from_db()
        assert delivery.status == S.RETRY
        assert expected_delay - 5 <= (delivery.next_attempt_at - before).total_seconds() <= expected_delay + 10
    Delivery.objects.update(next_attempt_at=timezone.now())
    dispatch_due()
    delivery.refresh_from_db()
    assert delivery.status == S.DEAD and delivery.attempt_count == 3
    assert len(sms.calls) == 3
    assert any(call.args[0] == 'admin_notification.dead' for call in alerts.call_args_list)


def test_insufficient_balance_blocks_sms_only_alerts_and_is_not_retried(monkeypatch, world, alerts):
    sms = FakeSms(script=lambda r, m, n: _fail(base.INSUFFICIENT_BALANCE))
    providers = patch_providers(monkeypatch, sms=sms)
    order = _order(world)
    dispatch_due()
    sms_row = Delivery.objects.get(channel='SMS')
    assert sms_row.status == S.FAILED and sms_row.last_error_class == 'INSUFFICIENT_BALANCE'
    assert set(Delivery.objects.filter(channel='WHATSAPP').values_list('status', flat=True)) == {S.SENT}
    assert len(providers.whatsapp.calls) == 3
    Delivery.objects.update(next_attempt_at=timezone.now() - timedelta(hours=1))
    dispatch_due()
    assert len(sms.calls) == 1  # no retry loop against an empty balance
    assert any(c.kwargs.get('level') == 'fatal' and 'BALANCE' in c.args[1] for c in alerts.call_args_list)
    assert Order.objects.filter(pk=order.pk).exists()


def test_ambiguous_timeout_is_unknown_and_not_resent(monkeypatch, world, alerts):
    sms = FakeSms(script=lambda r, m, n: _fail(base.TIMEOUT_AFTER_SEND, base.UNKNOWN))
    patch_providers(monkeypatch, sms=sms)
    _order(world)
    dispatch_due()
    Delivery.objects.update(next_attempt_at=timezone.now() - timedelta(hours=1))
    dispatch_due()
    assert Delivery.objects.get(channel='SMS').status == S.UNKNOWN
    assert len(sms.calls) == 1


def test_multi_part_delivery_resumes_after_the_last_accepted_part(monkeypatch, settings, world):
    settings.ADMIN_SMS_MAX_CHARS_PER_MESSAGE = 300
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False

    def script(recipient, message, n):
        if n == 1:
            return _fail(base.PROVIDER_5XX, base.RETRYABLE)
        return base.SendResult(base.ACCEPTED, provider_message_id=f'sms-part-{n}')
    sms = FakeSms(script=script)
    patch_providers(monkeypatch, sms=sms)
    _order(world)
    dispatch_due()
    delivery = Delivery.objects.get()
    assert delivery.status == S.RETRY and delivery.parts_sent == 1 and delivery.parts_total >= 3
    Delivery.objects.update(next_attempt_at=timezone.now())
    dispatch_due()
    delivery.refresh_from_db()
    assert delivery.status == S.SUBMITTED and delivery.parts_sent == delivery.parts_total
    sent = [m for _, m in sms.calls]
    assert sent[1] == sent[2]  # the failed part is retried ...
    assert sent.count(sent[0]) == 1  # ... the accepted part 1 is never repeated
    assert delivery.provider_messages.count() == delivery.parts_total


def test_unsent_alerts_expire_instead_of_arriving_a_day_late(monkeypatch, settings, world, alerts):
    providers = patch_providers(monkeypatch)
    _order(world)
    Delivery.objects.update(created_at=timezone.now() - timedelta(hours=13))
    result = dispatch_due()
    assert result.expired == 4
    assert set(Delivery.objects.values_list('status', flat=True)) == {S.DEAD}
    assert providers.sms.calls == [] and providers.whatsapp.calls == []


def test_worker_crash_mid_send_becomes_unknown(monkeypatch, world, alerts):
    patch_providers(monkeypatch)
    _order(world)
    Delivery.objects.filter(channel='SMS').update(status=S.SENDING, claimed_at=timezone.now() - timedelta(hours=1))
    result = dispatch_due()
    assert result.recovered == 1
    assert Delivery.objects.get(channel='SMS').last_error_class == 'WORKER_INTERRUPTED'


def test_update_waits_for_earlier_alert_to_the_same_admin(monkeypatch, world):
    calls = []
    sms = FakeSms(script=lambda r, m, n: (calls.append(m), base.SendResult(
        base.ACCEPTED, provider_message_id=f'id-{n}'))[1])
    patch_providers(monkeypatch, sms=sms)
    order = _order(world)
    Delivery.objects.update(status=S.RETRY, next_attempt_at=timezone.now() + timedelta(minutes=5))
    client_for(world.customer).patch(reverse('order-lifecycle-cancel', kwargs={'pk': order.pk}), {}, format='json')
    dispatch_due()
    assert calls == []  # CANCELLED must not overtake NEW ORDER
    Delivery.objects.filter(event__event_type='NEW_ORDER').update(next_attempt_at=timezone.now())
    dispatch_due()
    dispatch_due()
    assert [c.split('\n')[0] for c in calls] == [f'SIMAME NEW ORDER {order.order_no}', 'SIMAME ORDER CANCELLED - DO NOT DISPATCH A RIDER']


def test_kill_switch_stops_sending_without_touching_orders(monkeypatch, settings, world):
    providers = patch_providers(monkeypatch)
    order = _order(world)
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False
    dispatch_due()
    assert providers.sms.calls == [] and providers.whatsapp.calls == []
    assert set(Delivery.objects.values_list('status', flat=True)) == {S.PENDING}
    assert book(world).status_code == 201
    assert Order.objects.filter(pk=order.pk).exists()


def test_disabling_whatsapp_does_not_disable_sms(monkeypatch, settings, world):
    providers = patch_providers(monkeypatch)
    _order(world)
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    dispatch_due()
    assert len(providers.sms.calls) == 1 and providers.whatsapp.calls == []


def test_missing_whatsapp_configuration_is_reported_not_faked(settings, world, alerts):
    settings.ARKESEL_WHATSAPP_API_TOKEN = ''
    _order(world)
    with patch('admin_notifications.providers.http.requests.post') as post:
        post.side_effect = AssertionError('must not be called for WhatsApp')
        with patch('admin_notifications.services.dispatcher.get_sms_provider', lambda: FakeSms()):
            dispatch_due()
    rows = Delivery.objects.filter(channel='WHATSAPP')
    assert set(rows.values_list('status', flat=True)) == {S.FAILED}
    assert set(rows.values_list('last_error_class', flat=True)) == {'CONFIG_MISSING'}
    assert Delivery.objects.get(channel='SMS').status == S.SUBMITTED


def test_operator_retry_requeues_failed_and_does_not_repeat_accepted_parts(monkeypatch, world, alerts):
    wa = FakeWhatsApp(script=lambda r, k, p, n: _fail(base.AUTH_FAILED))
    patch_providers(monkeypatch, whatsapp=wa)
    _order(world)
    dispatch_due()
    assert set(Delivery.objects.filter(channel='WHATSAPP').values_list('status', flat=True)) == {S.FAILED}
    good = patch_providers(monkeypatch)
    assert requeue(Delivery.objects.all()) == 3
    dispatch_due()
    assert set(Delivery.objects.filter(channel='WHATSAPP').values_list('status', flat=True)) == {S.SENT}
    assert len(good.whatsapp.calls) == 3 and good.sms.calls == []


@pytest.mark.parametrize('sms_error,wa_error', [
    (base.NETWORK, None), (None, base.NETWORK), (base.NETWORK, base.NETWORK),
    (base.AUTH_FAILED, base.AUTH_FAILED), (base.INSUFFICIENT_BALANCE, None),
])
def test_failure_drill_orders_always_succeed_and_failures_are_observable(monkeypatch, world, alerts,
                                                                         sms_error, wa_error):
    def result(error):
        if error is None:
            return base.SendResult(base.ACCEPTED, provider_message_id='ok')
        return _fail(error, base.RETRYABLE if error == base.NETWORK else base.FAILED)
    patch_providers(monkeypatch, sms=FakeSms(script=lambda r, m, n: result(sms_error)),
                    whatsapp=FakeWhatsApp(script=lambda r, k, p, n: result(wa_error)))
    response = book(world)
    assert response.status_code == 201
    dispatch_due()
    order = Order.objects.get(pk=response.data['id'])
    assert order.items.count() == 2
    event = Event.objects.get(order=order)
    for delivery in event.deliveries.all():
        error = sms_error if delivery.channel == 'SMS' else wa_error
        if error is None:
            assert delivery.status in (S.SENT, S.SUBMITTED)
        else:
            assert delivery.status in (S.RETRY, S.FAILED) and delivery.last_error_class == error
    # The order is visible to ops in Django admin regardless.
    assert Order.objects.filter(order_no=order.order_no).exists()


def test_dispatch_command_dry_run_sends_nothing_and_masks_numbers(monkeypatch, world):
    providers = patch_providers(monkeypatch)
    _order(world)
    out = StringIO()
    call_command('dispatch_admin_notifications', '--dry-run', stdout=out)
    text = out.getvalue()
    assert 'SIMAME NEW ORDER' in text and '233 55 *** 7139' in text and '233551057139' not in text
    assert providers.sms.calls == [] and set(Delivery.objects.values_list('status', flat=True)) == {S.PENDING}


def test_dispatch_command_sends_and_filters_by_channel(monkeypatch, world):
    providers = patch_providers(monkeypatch)
    _order(world)
    out = StringIO()
    call_command('dispatch_admin_notifications', '--channel', 'sms', '--limit', '10', stdout=out)
    assert 'accepted=1' in out.getvalue()
    assert len(providers.sms.calls) == 1 and providers.whatsapp.calls == []


def test_dispatch_command_retry_failed(monkeypatch, world, alerts):
    patch_providers(monkeypatch, sms=FakeSms(script=lambda r, m, n: _fail(base.AUTH_FAILED)))
    _order(world)
    dispatch_due()
    providers = patch_providers(monkeypatch)
    call_command('dispatch_admin_notifications', '--retry-failed', stdout=StringIO())
    assert Delivery.objects.get(channel='SMS').status == S.SUBMITTED
    assert len(providers.sms.calls) == 1


def test_burst_of_fifty_bookings_stays_fast_with_slow_providers(monkeypatch, world):
    import time
    patch_providers(monkeypatch, sms=FakeSms(delay=0.2), whatsapp=FakeWhatsApp(delay=0.2))
    started = time.monotonic()
    for _ in range(50):
        assert book(world).status_code == 201
    elapsed = time.monotonic() - started
    # Providers are off the booking path entirely: 50 bookings x 4 slow sends
    # would take 40s if they were on it.
    assert elapsed < 20
    assert Event.objects.count() == 50 and Delivery.objects.count() == 200
    result = dispatch_due(limit=25)
    assert result.claimed == 25  # bounded batches
