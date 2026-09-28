"""What admins actually read: content, secrecy, snapshots, time zone, limits."""
import copy
import re
import json
from datetime import datetime, timedelta, timezone as dt_tz
from decimal import Decimal

import pytest
from django.utils import timezone

from admin_notifications.models import AdminNotificationEvent as Event
from admin_notifications.rendering import (
    NEW_ORDER_TEMPLATE_BODY, ORDER_UPDATE_TEMPLATE_BODY, fmt_dt, render_sms_parts, render_whatsapp_parts,
    sms_segments, split_sms, to_sms_text,
)
from admin_notifications.tests.helpers import book, configure, make_world
from ordering.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(settings):
    configure(settings)
    return make_world(first_name='Philip', last_name='Ayesu')


def _booked(world, **kwargs):
    response = book(world, **kwargs)
    assert response.status_code == 201, response.data
    order = Order.objects.get(pk=response.data['id'])
    return order, Event.objects.get(order=order, event_type='NEW_ORDER')


def _all_text(payload):
    sms = '\n'.join(render_sms_parts(payload))
    whatsapp = '\n'.join(part.body for part in render_whatsapp_parts(payload))
    return sms, whatsapp


def test_known_fixture_contains_every_operational_field(world):
    pickup = (timezone.now() + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    order, event = _booked(world, pickup_date=pickup, pickup_address='KNUST Ayeduase, opposite Pharmacy',
                           delivery_address='Brunei Hostel Block B, KNUST', notes='Handle silk gently')
    customer_phone = world.customer.phone
    national = '0' + customer_phone[-9:] if customer_phone.startswith('233') else customer_phone
    for text in _all_text(event.payload):
        assert order.order_no in text
        assert 'Philip Ayesu' in text
        assert f'{national[:3]} {national[3:6]} {national[6:]}' in text
        assert world.laundry.name in text and '024 111 2233' in text
        assert 'KNUST Ayeduase, opposite Pharmacy' in text
        assert 'Brunei Hostel Block B, KNUST' in text
        assert '3:00 PM - 4:00 PM GMT' in text  # Accra time, 1h window
        assert f"{pickup.day} {pickup.strftime('%b %Y')}" in text
        assert 'Not scheduled yet' in text  # booking API does not take a delivery date
        variant = world.service.service_type.name  # service variant, e.g. "Wash & Iron"
        assert f'2 x {world.service.item.name} ({variant}) @ GHS 10.00 = GHS 20.00' in text
        assert f'1 x {world.service2.item.name} ({variant}) = GHS 45.00' in text
        assert f'TOTAL: GHS {order.total_amount}' in text
        assert 'CASH ON DELIVERY' in text and 'NOT YET PAID' in text
        assert f'Amount to collect: GHS {order.total_amount}' in text
        assert f'Outstanding: GHS {order.total_amount}' in text and 'Paid: GHS 0.00' in text
        assert 'Transport: not billed in the app' in text
        assert 'Handle silk gently' in text
        assert f'/admin/ordering/order/{order.pk}/change/' in text
        assert 'https://www.google.com/maps/search/?api=1&query=5.604000,-0.186000' in text


def test_messages_never_contain_secrets(settings, world):
    order, event = _booked(world)
    Order.objects.filter(pk=order.pk).update(handover_code='4821')
    order.refresh_from_db()
    from admin_notifications.services.outbox import emit_order_cancelled
    Order.objects.filter(pk=order.pk).update(status='CANCELLED', cancelled_at=timezone.now())
    emit_order_cancelled(Order.objects.get(pk=order.pk), 'PENDING', 'CANCELLED')
    settings.PAYSTACK_SECRET_KEY = 'sk_live_paystack_secret'
    blob = json.dumps([e.payload for e in Event.objects.filter(order=order)])
    rendered = '\n'.join('\n'.join(_all_text(e.payload)) for e in Event.objects.filter(order=order))
    for text in (blob, rendered):
        assert '4821' not in text  # the customer's delivery handover code
        for secret in ('sk_live_paystack_secret', 'ark-test-key-DO-NOT-LEAK', 'wa-token-DO-NOT-LEAK',
                       'authorization_code', 'Bearer ', 'eyJ', 'password'):
            assert secret not in text


def test_snapshot_keeps_booking_price_after_catalogue_change(world):
    order, event = _booked(world)
    world.service.price = Decimal('15.00')
    world.service.save()
    event.refresh_from_db()
    sms, whatsapp = _all_text(event.payload)
    assert '@ GHS 10.00 = GHS 20.00' in sms and '@ GHS 10.00 = GHS 20.00' in whatsapp
    assert 'GHS 15.00' not in sms


def test_by_weight_is_labelled_as_an_estimate(world):
    _, event = _booked(world, mode='BY_WEIGHT', weight='4.00')
    for text in _all_text(event.payload):
        assert 'BY WEIGHT' in text
        assert 'Rate: GHS 20.00/kg' in text
        assert 'Estimated weight: 4.00 kg' in text
        assert 'Estimated charge: GHS 80.00' in text
        assert 'Final charge: NOT CONFIRMED' in text
        assert 'ESTIMATED TOTAL' in text
        assert '(ESTIMATE, final after weigh-in)' in text  # COD amount to collect
        # Never presented as a confirmed amount.
        assert 'TOTAL: GHS' not in text.replace('ESTIMATED TOTAL: GHS', '')


def test_weight_tariff_shown_to_admins_is_the_booking_tariff(world):
    order, _ = _booked(world, mode='BY_WEIGHT', weight='4.00')
    world.weight_pricing.base_price_per_kg = Decimal('99.00')
    world.weight_pricing.save()
    from admin_notifications.services.outbox import emit_order_cancelled
    Order.objects.filter(pk=order.pk).update(status='CANCELLED', cancelled_at=timezone.now())
    emit_order_cancelled(Order.objects.get(pk=order.pk), 'PENDING', 'CANCELLED')
    cancelled = Event.objects.get(order=order, event_type='ORDER_CANCELLED')
    assert cancelled.payload['weight']['rate_per_kg'] == '20.00'


def test_custom_quote_says_no_price_yet(world):
    _, event = _booked(world, mode='CUSTOM_QUOTE')
    for text in _all_text(event.payload):
        assert 'PAY AFTER QUOTE' in text
        assert 'awaiting laundry quote' in text


def test_missing_coordinates_show_address_and_no_invented_map(world):
    _, event = _booked(world, coords=False, pickup_address='House 5, Asokwa, near the Total station')
    for text in _all_text(event.payload):
        assert 'House 5, Asokwa, near the Total station' in text
        assert 'google.com/maps' not in text
        assert 'no coordinates stored' in text


def test_distinct_delivery_location_gets_its_own_map(world):
    response = book(world, delivery_address='Brunei Hostel', coords=True)
    order = Order.objects.get(pk=response.data['id'])
    Order.objects.filter(pk=order.pk).update(delivery_lat=Decimal('5.6200000'))
    payload = copy.deepcopy(Event.objects.get(order=order).payload)
    payload['delivery'].update(lat='5.620000', lng='-0.186000', same_as_pickup=False)
    sms, _ = _all_text(payload)
    assert 'query=5.620000,-0.186000' in sms


def test_fees_discount_and_zero_lines(world):
    _, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['money'].update(pickup_fee='5.00', delivery_fee='7.50', discount='3.00', coupon_code='WELCOME',
                            logistics_discount='0.00', total='74.50', transport_in_app=True,
                            transport_charged='12.50', rider_cost='14.00')
    sms, whatsapp = _all_text(payload)
    for text in (sms, whatsapp):
        assert 'Transport charged: GHS 12.50 (pickup GHS 5.00, delivery GHS 7.50)' in text
        assert 'Rider cost: GHS 14.00' in text
        assert 'Discount (WELCOME): -GHS 3.00' in text
        assert 'Transport promo' not in text  # zero lines are omitted


def test_africa_accra_time_regardless_of_server_zone():
    assert fmt_dt('2026-09-25T15:22:00+00:00') == 'Fri 25 Sep 2026, 3:22 PM GMT'
    assert fmt_dt('2026-09-25T16:22:00+01:00') == 'Fri 25 Sep 2026, 3:22 PM GMT'
    assert fmt_dt(datetime(2026, 9, 25, 0, 5, tzinfo=dt_tz.utc).isoformat()) == 'Fri 25 Sep 2026, 12:05 AM GMT'


def test_whatsapp_long_order_overflows_into_labelled_parts_without_losing_items(world):
    order, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['items'] = [
        {'name': f'Special garment number {i} with a long description', 'quantity': 1,
         'unit_price': '12.00', 'line_total': '12.00'} for i in range(40)
    ]
    payload['notes'] = 'Please ' + 'be careful with the lace. ' * 20
    parts = render_whatsapp_parts(payload)
    assert len(parts) >= 3
    assert parts[0].template_key == 'new_order' and 'See next message' in parts[0].params
    total = len(parts)
    for number, part in enumerate(parts[1:], start=2):
        assert part.template_key == 'order_update'
        assert part.params[0] == f'NEW ORDER DETAILS ({number}/{total})'
    joined = ' '.join(' '.join(p.params) for p in parts)
    for i in range(40):
        assert f'Special garment number {i} with' in joined
    for part in parts:
        assert len(part.body) <= 1024
        for param in part.params:
            assert '\n' not in param and '\t' not in param and '     ' not in param and param


def test_update_template_parameters_are_legal_and_bounded(world):
    _, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['event_type'] = 'ORDER_CANCELLED'
    payload['event'] = {'from_status': 'CONFIRMED', 'reason': 'Line one\nline two\t\ttabbed', 'at': None}
    parts = render_whatsapp_parts(payload)
    assert parts[0].params[0] == 'ORDER CANCELLED - DO NOT DISPATCH A RIDER'
    for part in parts:
        assert len(part.body) <= 1024 and all('\n' not in p for p in part.params)
    assert 'Line one | line two tabbed' in parts[0].params[2]


def test_template_bodies_start_and_end_with_fixed_text():
    for body in (NEW_ORDER_TEMPLATE_BODY, ORDER_UPDATE_TEMPLATE_BODY):
        assert not body.startswith('{{') and not body.rstrip().endswith('}}')


def test_long_sms_is_split_into_numbered_parts_and_nothing_is_dropped(settings, world):
    settings.ADMIN_SMS_MAX_CHARS_PER_MESSAGE = 400
    _, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['items'] = [{'name': f'Item {i}', 'quantity': 2, 'unit_price': '5.00', 'line_total': '10.00'}
                        for i in range(25)]
    parts = render_sms_parts(payload)
    order_no = payload['order']['order_no']
    assert len(parts) > 1
    for i, part in enumerate(parts, start=1):
        assert part.startswith(f'SIMAME ORDER {order_no} ({i}/{len(parts)})')
        assert len(part) <= 400
    joined = '\n'.join(parts)
    for i in range(25):
        assert f'2 x Item {i} @ GHS 5.00' in joined


def test_split_sms_short_text_is_untouched():
    assert split_sms('hello', 'CN-1', 918) == ['hello']


def test_sms_text_is_gsm_friendly():
    text = to_sms_text('Total ₵ 20 \U0001F9FA “quoted” – café ça Kofí')
    assert '\U0001F9FA' not in text and '₵' not in text
    # Emoji dropped, cedi -> GHS, smart quotes/dash straightened; GSM-7 letters
    # (é) kept, others folded to ASCII (ç -> c, í -> i).
    assert text == 'Total GHS 20  "quoted" - café ca Kofi'
    assert sms_segments('a' * 160) == 1 and sms_segments('a' * 161) == 2
    assert sms_segments('क' * 70) == 1 and sms_segments('क' * 71) == 2


def test_compact_mode_is_short(settings, world):
    settings.ADMIN_SMS_DETAIL_MODE = 'compact'
    _, event = _booked(world)
    (part,) = render_sms_parts(event.payload)
    assert len(part) < 400 and 'CASH ON DELIVERY' in part


def test_staging_messages_are_marked_and_production_are_not(settings, world):
    _, event = _booked(world)
    assert '[STAGING]' not in '\n'.join(_all_text(event.payload))
    staging = copy.deepcopy(event.payload)
    staging['environment'] = 'staging'
    sms, whatsapp = _all_text(staging)
    assert sms.startswith('[STAGING] SIMAME') and '[STAGING] CN-' in whatsapp


def test_payment_confirmed_message(world):
    _, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['event_type'] = 'PAYMENT_CONFIRMED'
    payload['payment'].update(is_cod=False, status='PAID', method_label='PAYSTACK', channel='Card',
                              reference='ORD-abc-123', paid_at='2026-09-25T15:22:00+00:00')
    payload['event'] = {'amount': '72.80'}
    sms, whatsapp = _all_text(payload)
    for text in (sms, whatsapp):
        assert 'PAYMENT CONFIRMED' in text and 'GHS 72.80' in text and 'ORD-abc-123' in text
        assert 'Card via Paystack' in text and '3:22 PM GMT' in text
        assert 'NEW ORDER' not in text


def test_reschedule_message_warns_against_stale_schedule(world):
    _, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['event_type'] = 'ORDER_RESCHEDULED'
    payload['event'] = {'changes': [{'field': 'pickup_date', 'label': 'Pickup time',
                                     'old': 'Mon 28 Sep 2026, 5:00 PM GMT', 'new': 'Tue 29 Sep 2026, 9:00 AM GMT'}]}
    sms, whatsapp = _all_text(payload)
    for text in (sms, whatsapp):
        assert 'UPDATED ORDER - DO NOT USE PREVIOUS SCHEDULE' in text
        assert 'Pickup time: Mon 28 Sep 2026, 5:00 PM GMT -> Tue 29 Sep 2026, 9:00 AM GMT' in text


@pytest.mark.parametrize('lines', [12, 24, 36])
def test_long_orders_split_only_between_lines(settings, world, lines):
    """10+/20+/30+ line orders: every item line, price and reference stays whole
    in exactly one SMS part and one WhatsApp part."""
    settings.ADMIN_SMS_MAX_CHARS_PER_MESSAGE = 459  # 3 GSM segments, forces splits
    _, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['items'] = [
        {'name': f'Kente cloth wrapper number {i} dry clean only', 'quantity': 3, 'unit_price': '17.50',
         'line_total': '52.50'} for i in range(lines)
    ]
    order_no = payload['order']['order_no']
    sms_parts = render_sms_parts(payload)
    wa_parts = render_whatsapp_parts(payload)
    assert len(sms_parts) > 1 and len(wa_parts) > 1
    for i in range(lines):
        line = f'3 x Kente cloth wrapper number {i} dry clean only @ GHS 17.50 = GHS 52.50'
        assert sum(line in part for part in sms_parts) == 1, line
        assert sum(line in ' '.join(p.params) for p in wa_parts) == 1, line
    for part in sms_parts:
        assert len(part) <= 459 and order_no in part
    for part in wa_parts:
        assert len(part.body) <= 1024 and order_no in part.body
    # Customer name and money tokens never end up cut.
    assert sum('Philip Ayesu' in part for part in sms_parts) >= 1
    for part in sms_parts:
        # A currency code is never left dangling at a line or part end.
        assert not re.search(r'GHS\s*$', part) and not re.search(r'GHS\n', part)


def test_paystack_pending_and_paid_wording(world):
    _, event = _booked(world)
    payload = copy.deepcopy(event.payload)
    payload['payment'].update(is_cod=False, method='CARD', method_label='PAYSTACK', status='PENDING',
                              status_label='PENDING (not yet confirmed by Paystack)')
    for text in _all_text(payload):
        assert 'PAYSTACK' in text and 'PENDING' in text
        assert 'Wait for PAYMENT CONFIRMED before dispatching a rider' in text
