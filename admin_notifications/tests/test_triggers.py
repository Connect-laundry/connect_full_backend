"""Which business events create admin alerts, exactly once, without ever
blocking the order. Runs through the real HTTP endpoints."""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from admin_notifications.models import AdminNotificationDelivery as Delivery
from admin_notifications.models import AdminNotificationEvent as Event
from admin_notifications.services import outbox
from admin_notifications.tests.helpers import (
    SMS_NORMALIZED, WA_NORMALIZED, book, client_for, configure, make_world,
)
from ordering.models import Order
from payments.models import Payment

pytestmark = pytest.mark.django_db
ET = Event.EventType


@pytest.fixture
def world(settings):
    configure(settings)
    return make_world()


def _events(order, event_type=None):
    qs = Event.objects.filter(order=order)
    return qs.filter(event_type=event_type) if event_type else qs


# --- NEW_ORDER --------------------------------------------------------------------

def test_cod_booking_creates_new_order_alert_without_waiting_for_payment(world):
    response = book(world, method='CASH')
    assert response.status_code == 201
    order = Order.objects.get(pk=response.data['id'])
    assert order.payment_status == Order.PaymentStatus.UNPAID

    event = _events(order).get()
    assert event.event_type == ET.NEW_ORDER
    assert event.idempotency_key == f'NEW_ORDER:{order.pk}'
    assert event.payload['payment']['is_cod'] is True
    assert event.payload['payment']['status'] == 'NOT_YET_PAID'
    assert event.payload['payment']['method_label'] == 'CASH ON DELIVERY'

    deliveries = Delivery.objects.filter(event=event)
    assert sorted(deliveries.filter(channel='WHATSAPP').values_list('recipient', flat=True)) == sorted(WA_NORMALIZED)
    assert list(deliveries.filter(channel='SMS').values_list('recipient', flat=True)) == [SMS_NORMALIZED]
    assert set(deliveries.values_list('status', flat=True)) == {'PENDING'}


@pytest.mark.parametrize('alias', ['cash_on_delivery', 'COD', 'CASH'])
def test_pay_on_delivery_aliases_all_alert_as_cod(world, alias):
    order = Order.objects.get(pk=book(world, method=alias).data['id'])
    assert _events(order, ET.NEW_ORDER).get().payload['payment']['method_label'] == 'CASH ON DELIVERY'


@patch('payments.services.paystack.PaystackService.initialize_transaction')
def test_paystack_booking_alerts_immediately_with_pending_status(mock_init, world):
    mock_init.return_value = {'status': True, 'data': {'access_code': 'AC_x', 'authorization_url': 'https://x'}}
    response = book(world, method='CARD')
    order = Order.objects.get(pk=response.data['id'])
    payload = _events(order, ET.NEW_ORDER).get().payload
    assert payload['payment']['status'] == 'PENDING'
    assert payload['payment']['verified'] is False
    assert payload['payment']['method_label'] == 'PAYSTACK'


@patch('payments.services.paystack.PaystackService.initialize_transaction')
def test_booking_succeeds_when_paystack_initialisation_fails_and_alert_still_recorded(mock_init, world):
    mock_init.side_effect = RuntimeError('paystack down')
    response = book(world, method='CARD')
    assert response.status_code == 201
    assert _events(Order.objects.get(pk=response.data['id']), ET.NEW_ORDER).count() == 1


def test_by_weight_booking_snapshots_tariff_and_marks_estimate(world):
    response = book(world, mode='BY_WEIGHT', weight='4.00')
    assert response.status_code == 201, response.data
    payload = _events(Order.objects.get(pk=response.data['id'])).get().payload
    assert payload['weight']['rate_per_kg'] == '20.00'
    assert payload['weight']['estimated_kg'] == '4.00'
    assert payload['money']['is_estimate'] is True
    assert payload['money']['items_total'] == '80.00'


def test_custom_quote_booking_alerts_with_awaiting_quote(world):
    response = book(world, mode='CUSTOM_QUOTE')
    payload = _events(Order.objects.get(pk=response.data['id'])).get().payload
    assert payload['money']['awaiting_quote'] is True
    assert payload['items'] == []


def test_rejected_booking_records_no_alert(world):
    # Stale price: the serializer raises after creating the order, rolling
    # the whole transaction back, alert included.
    response = book(world)
    assert response.status_code == 201
    before = Event.objects.count()
    body_resp = client_for(world.customer).post(reverse('booking-create'), {
        'laundry': str(world.laundry.id),
        'pickup_date': (timezone.now() + timedelta(days=1)).isoformat(),
        'pickup_address': 'A', 'delivery_address': 'A', 'payment_method': 'CASH',
        'items': [{'item': str(world.service.item_id), 'service_type': str(world.service.service_type_id),
                   'quantity': 1}],
        'expected_total': '1.00',
    }, format='json')
    assert body_resp.status_code == 400
    assert Event.objects.count() == before


def test_feature_flag_off_records_nothing(settings, world):
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False
    response = book(world)
    assert response.status_code == 201
    assert Event.objects.count() == 0


def test_channels_are_independent(settings, world):
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = False
    order = Order.objects.get(pk=book(world).data['id'])
    assert list(Delivery.objects.filter(event__order=order).values_list('channel', flat=True)) == ['SMS']
    settings.ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED = True
    settings.ADMIN_SMS_NOTIFICATIONS_ENABLED = False
    order = Order.objects.get(pk=book(world).data['id'])
    assert set(Delivery.objects.filter(event__order=order).values_list('channel', flat=True)) == {'WHATSAPP'}


def test_alert_failure_never_breaks_the_booking(world):
    with patch('admin_notifications.services.outbox.build_order_snapshot', side_effect=RuntimeError('boom')), \
            patch('admin_notifications.services.metrics.alert') as alert:
        response = book(world)
    assert response.status_code == 201
    order = Order.objects.get(pk=response.data['id'])
    assert order.items.count() == 2 and order.total_amount > 0
    assert Event.objects.count() == 0
    assert alert.called


def test_missing_outbox_table_mid_deploy_never_breaks_the_booking(world):
    from django.db import DatabaseError
    with patch.object(Event.objects, 'filter', side_effect=DatabaseError('relation does not exist')), \
            patch('admin_notifications.services.metrics.alert'):
        response = book(world)
    assert response.status_code == 201
    assert Order.objects.filter(pk=response.data['id']).exists()


# --- idempotency --------------------------------------------------------------------

def test_twenty_emits_for_the_same_order_create_one_event_and_four_deliveries(world):
    order = Order.objects.get(pk=book(world).data['id'])
    for _ in range(20):
        outbox.emit_new_order(order)
    assert _events(order, ET.NEW_ORDER).count() == 1
    assert Delivery.objects.filter(event__order=order, channel='WHATSAPP').count() == 3
    assert Delivery.objects.filter(event__order=order, channel='SMS').count() == 1


def test_database_enforces_event_and_delivery_uniqueness(world):
    from django.db import IntegrityError, transaction
    order = Order.objects.get(pk=book(world).data['id'])
    event = _events(order).get()
    with pytest.raises(IntegrityError), transaction.atomic():
        Event.objects.create(order=order, event_type=ET.NEW_ORDER, idempotency_key=event.idempotency_key,
                             payload={}, environment='test')
    first = Delivery.objects.filter(event=event).first()
    with pytest.raises(IntegrityError), transaction.atomic():
        Delivery.objects.create(event=event, channel=first.channel, recipient=first.recipient, provider='x',
                                dedup_key=first.dedup_key + 'x')


def test_idempotent_booking_retry_with_same_key_creates_one_alert(world):
    headers = {'HTTP_X_IDEMPOTENCY_KEY': 'retry-key-1'}
    pickup = (timezone.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    first = book(world, headers=headers, pickup_date=pickup)
    second = book(world, headers=headers, pickup_date=pickup)
    assert first.status_code == 201 and second.status_code == 201
    assert first.data['id'] == second.data['id']
    assert Event.objects.filter(event_type=ET.NEW_ORDER).count() == 1


# --- PAYMENT_CONFIRMED ------------------------------------------------------------

def _pending_card_order(world):
    with patch('payments.services.paystack.PaystackService.initialize_transaction') as init:
        init.return_value = {'status': True, 'data': {'access_code': 'AC', 'authorization_url': 'https://x'}}
        order = Order.objects.get(pk=book(world, method='CARD').data['id'])
    payment = Payment.objects.get(order=order)
    return order, payment


def _signed_webhook(payment, event_id):
    data = {'id': event_id, 'status': 'success', 'reference': payment.transaction_reference,
            'amount': int(payment.amount * 100), 'currency': 'GHS', 'channel': 'mobile_money',
            'authorization': {'authorization_code': 'AUTH_supersecret'},
            'metadata': {'order_id': str(payment.order_id), 'user_id': str(payment.user_id)}}
    body = json.dumps({'event': 'charge.success', 'data': data}, separators=(',', ':')).encode()
    sig = hmac.new(b'test-paystack-secret', body, hashlib.sha512).hexdigest()
    return APIClient().post(reverse('paystack_webhook'), data=body, content_type='application/json',
                            HTTP_X_PAYSTACK_SIGNATURE=sig)


def test_paystack_webhook_emits_payment_confirmed_once_and_never_a_second_new_order(settings, world):
    settings.PAYSTACK_SECRET_KEY = 'test-paystack-secret'
    settings.PAYMENT_CURRENCY = 'GHS'
    order, payment = _pending_card_order(world)
    for n in range(3):  # Paystack retries and a replayed event id
        assert _signed_webhook(payment, f'evt-{n % 2}').status_code == 200
    order.refresh_from_db()
    assert order.payment_status == 'PAID'
    assert _events(order, ET.NEW_ORDER).count() == 1
    confirmed = _events(order, ET.PAYMENT_CONFIRMED).get()
    assert confirmed.payload['payment']['verified'] is True
    assert confirmed.payload['payment']['channel'] == 'Mobile Money'
    assert confirmed.payload['payment']['reference'] == payment.transaction_reference
    assert 'AUTH_supersecret' not in json.dumps(confirmed.payload)
    assert Delivery.objects.filter(event=confirmed).count() == 4


@patch('payments.services.paystack.PaystackService.verify_transaction')
def test_verify_endpoint_emits_payment_confirmed(mock_verify, world):
    order, payment = _pending_card_order(world)
    mock_verify.return_value = {'status': True, 'data': {
        'status': 'success', 'reference': payment.transaction_reference, 'amount': int(payment.amount * 100),
        'currency': 'GHS', 'metadata': {'order_id': str(order.pk), 'user_id': str(order.user_id)}}}
    client = client_for(world.customer)
    for _ in range(2):
        response = client.get(reverse('payment_verify', kwargs={'reference': payment.transaction_reference}))
        assert response.status_code == 200, response.data
    assert _events(order, ET.PAYMENT_CONFIRMED).count() == 1


def test_frontend_cannot_mark_paid_unverified_payment_emits_nothing(world):
    order, _ = _pending_card_order(world)
    outbox.emit_payment_confirmed(order)  # order still UNPAID in the database
    assert _events(order, ET.PAYMENT_CONFIRMED).count() == 0


def test_cod_payment_confirmed_only_when_cash_is_collected(world):
    order = Order.objects.get(pk=book(world, method='CASH').data['id'])
    assert _events(order, ET.PAYMENT_CONFIRMED).count() == 0
    Order.objects.filter(pk=order.pk).update(status=Order.Status.OUT_FOR_DELIVERY)
    owner = client_for(world.owner)
    url = reverse('order-lifecycle-collect-cash', kwargs={'pk': order.pk})
    for _ in range(2):
        response = owner.post(url, {'amount': str(order.total_amount)}, format='json')
        assert response.status_code == 200, response.data
    event = _events(order, ET.PAYMENT_CONFIRMED).get()
    assert event.payload['payment']['status'] == 'CASH_COLLECTED'
    assert event.payload['event']['amount'] == str(order.total_amount)


# --- cancellation / rejection --------------------------------------------------

def test_customer_cancellation_alerts_once(world):
    order = Order.objects.get(pk=book(world).data['id'])
    client = client_for(world.customer)
    url = reverse('order-lifecycle-cancel', kwargs={'pk': order.pk})
    assert client.patch(url, {'reason': 'Travelling'}, format='json').status_code == 200
    client.patch(url, {'reason': 'Travelling'}, format='json')
    event = _events(order, ET.ORDER_CANCELLED).get()
    assert event.payload['event']['reason'] == 'Travelling'
    assert event.payload['event']['from_status'] == 'PENDING'


def test_laundry_rejection_alerts(world):
    order = Order.objects.get(pk=book(world).data['id'])
    url = reverse('order-lifecycle-reject', kwargs={'pk': order.pk})
    assert client_for(world.owner).patch(url, {'reason': 'Closed today'}, format='json').status_code == 200
    assert _events(order, ET.ORDER_REJECTED).get().payload['event']['reason'] == 'Closed today'


def test_ordinary_status_changes_do_not_alert(world):
    order = Order.objects.get(pk=book(world).data['id'])
    url = reverse('order-lifecycle-accept', kwargs={'pk': order.pk})
    assert client_for(world.owner).patch(url, {}, format='json').status_code == 200
    assert list(_events(order).values_list('event_type', flat=True)) == [ET.NEW_ORDER]


def test_update_events_skip_orders_that_never_had_a_booking_alert(settings, world):
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = False
    order = Order.objects.get(pk=book(world).data['id'])
    settings.ADMIN_ORDER_NOTIFICATIONS_ENABLED = True
    url = reverse('order-lifecycle-cancel', kwargs={'pk': order.pk})
    client_for(world.customer).patch(url, {}, format='json')
    assert Event.objects.count() == 0


# --- schedule / location changes ----------------------------------------------

def test_reschedule_alerts_with_old_and_new_values(world):
    order = Order.objects.get(pk=book(world).data['id'])
    order = Order.objects.get(pk=order.pk)
    old = order.pickup_date
    order.pickup_date = old + timedelta(days=1)
    order.save()
    event = _events(order, ET.ORDER_RESCHEDULED).get()
    change = event.payload['event']['changes'][0]
    assert change['label'] == 'Pickup time' and change['old'] != change['new']


def test_pickup_move_alerts_location_changed(world):
    order = Order.objects.get(pk=book(world).data['id'])
    order = Order.objects.get(pk=order.pk)
    order.pickup_address = 'Brunei Hostel, KNUST'
    order.pickup_lat = Decimal('5.6100000')
    order.save()
    event = _events(order, ET.PICKUP_LOCATION_CHANGED).get()
    labels = [c['label'] for c in event.payload['event']['changes']]
    assert 'Pickup address' in labels and 'Pickup map pin' in labels
    assert event.payload['pickup']['address'] == 'Brunei Hostel, KNUST'


def test_saves_that_change_nothing_do_not_alert(world):
    order = Order.objects.get(pk=Order.objects.get(pk=book(world).data['id']).pk)
    order.save()
    order.pickup_address = order.pickup_address + '  '  # whitespace only
    order.pickup_lat = Decimal(str(order.pickup_lat))  # same number
    order.save()
    order.special_instructions = 'new note'  # not an operational field
    order.save(update_fields=['special_instructions', 'updated_at'])
    assert list(_events(order).values_list('event_type', flat=True)) == [ET.NEW_ORDER]


def test_changes_after_delivery_do_not_alert(world):
    order = Order.objects.get(pk=book(world).data['id'])
    Order.objects.filter(pk=order.pk).update(status=Order.Status.DELIVERED)
    order = Order.objects.get(pk=order.pk)
    order.delivery_address = 'Somewhere else'
    order.save()
    assert _events(order).count() == 1


# --- price finalized -----------------------------------------------------------------

def test_quote_finalizes_price_and_alerts_once(world):
    order = Order.objects.get(pk=book(world, mode='CUSTOM_QUOTE').data['id'])
    url = reverse('order-lifecycle-quote', kwargs={'pk': order.pk})
    response = client_for(world.owner).post(
        url, {'items': [{'name': 'Bulk wash 3kg', 'quantity': 1, 'price': '60.00'}]}, format='json')
    assert response.status_code == 200, response.data
    event = _events(order, ET.ORDER_PRICE_FINALIZED).get()
    assert event.payload['event']['previous_total'] is None
    assert event.payload['items'][0]['line_total'] == '60.00'
    assert event.payload['money']['awaiting_quote'] is False

