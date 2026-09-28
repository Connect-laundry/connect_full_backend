"""OrderNotificationSnapshotBuilder: the frozen payload an alert is rendered from.

Built inside the transaction that changes the order, from the values stored
on the order and its lines (prices, fees and discounts frozen at booking), so
a later catalogue or tariff edit can never rewrite what the admins were told.

Deliberately excluded, whatever the order holds: the delivery handover code,
payment provider payloads and authorization codes, tokens and credentials.
The builder reads only the fields listed here.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from . import conf
from .phone import display_phone

PAYLOAD_VERSION = 1
ZERO = Decimal('0.00')


def money(value) -> str:
    try:
        return str(Decimal(str(value if value is not None else '0')).quantize(Decimal('0.01')))
    except (InvalidOperation, ValueError):
        return '0.00'


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else '0'))
    except (InvalidOperation, ValueError):
        return ZERO


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _coord(value, lo, hi) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not (Decimal(lo) <= number <= Decimal(hi)):
        return None
    return format(number.quantize(Decimal('0.000001')), 'f')


def _location(address, lat, lng) -> dict:
    lat_s = _coord(lat, -90, 90)
    lng_s = _coord(lng, -180, 180)
    if lat_s is None or lng_s is None:
        lat_s = lng_s = None
    return {'address': (address or '').strip(), 'lat': lat_s, 'lng': lng_s}


ADMIN_LINK_UNAVAILABLE = 'Admin link unavailable (ADMIN_NOTIFICATION_ADMIN_BASE_URL not configured)'


def admin_order_url(order_id) -> str:
    """Django admin change page. Requires a staff login; grants nothing by itself."""
    base = conf.admin_base_url()
    try:
        path = reverse('admin:ordering_order_change', args=[str(order_id)])
    except NoReverseMatch:
        path = f'/admin/ordering/order/{order_id}/change/'
    if base:
        return f'{base}{path}'
    # Production without a valid explicit origin: say so rather than send a
    # link to the wrong server.
    return ADMIN_LINK_UNAVAILABLE if conf.is_production() else path


def _pickup_window_end(order):
    """End of the pickup window the customer chose.

    Only the window start is stored. A published BookingSlot starting at that
    time gives the real end; otherwise the generated windows are one hour.
    """
    if not order.pickup_date:
        return None
    try:
        from ordering.models import BookingSlot
        slot = (
            BookingSlot.objects.filter(laundry_id=order.laundry_id, start_time=order.pickup_date)
            .order_by('end_time').first()
        )
        if slot and slot.end_time > slot.start_time:
            return slot.end_time
        from laundries.services.pickup_windows import WINDOW
        return order.pickup_date + WINDOW
    except Exception:
        return order.pickup_date + timedelta(hours=1)


def _payment_block(order, payment) -> dict:
    method = order.payment_method
    is_cod = method == 'CASH'
    paid = order.payment_status == 'PAID'
    awaiting_quote = order.pricing_mode == 'CUSTOM_QUOTE' and order.priced_at is None
    if is_cod:
        method_label = 'CASH ON DELIVERY'
        status_code = 'CASH_COLLECTED' if paid else 'NOT_YET_PAID'
        status_label = 'CASH COLLECTED' if paid else 'NOT YET PAID'
    else:
        method_label = 'PAYSTACK' if method == 'CARD' else 'PAYSTACK (BANK TRANSFER)'
        if order.payment_status == 'REFUNDED':
            status_code, status_label = 'REFUNDED', 'REFUNDED'
        elif paid:
            status_code, status_label = 'PAID', 'PAID (verified by Paystack)'
        elif awaiting_quote:
            status_code, status_label = 'AWAITING_QUOTE', 'NOT DUE YET (awaiting laundry quote)'
        else:
            status_code, status_label = 'PENDING', 'PENDING (not yet confirmed by Paystack)'
    channel = ''
    reference = ''
    paid_at = None
    if payment is not None and payment.status == 'SUCCESS':
        channel = {
            'MOBILE_MONEY': 'Mobile Money', 'CARD': 'Card', 'BANK_TRANSFER': 'Bank transfer',
            'CASH': 'Cash',
        }.get(payment.payment_method, '')
        # Our own public reference (ORD-...), never Paystack authorization data.
        reference = payment.transaction_reference or ''
        paid_at = _iso(payment.paid_at)
    return {
        'method': method,
        'method_label': method_label,
        'is_cod': is_cod,
        'status': status_code,
        'status_label': status_label,
        'verified': bool(paid and not is_cod),
        'channel': channel,
        'reference': reference,
        'paid_at': paid_at,
    }


def _weight_block(order, booking_payload) -> dict | None:
    if order.pricing_mode != 'BY_WEIGHT':
        return None
    if booking_payload and booking_payload.get('weight'):
        # Tariff as it stood at booking, from the NEW_ORDER snapshot.
        block = dict(booking_payload['weight'])
        block['estimated_kg'] = money(order.estimated_weight_kg) if order.estimated_weight_kg is not None else block.get('estimated_kg')
        return block
    block = {
        'estimated_kg': money(order.estimated_weight_kg) if order.estimated_weight_kg is not None else None,
        'rate_per_kg': None,
        'minimum_charge': None,
        'minimum_weight_kg': None,
        'rounding': '',
    }
    try:
        tariff = order.laundry.weight_pricing
    except Exception:
        tariff = None
    if tariff is not None:
        block.update({
            'rate_per_kg': money(tariff.base_price_per_kg),
            'minimum_charge': money(tariff.minimum_charge) if tariff.minimum_charge else None,
            'minimum_weight_kg': money(tariff.minimum_order_weight_kg) if tariff.minimum_order_weight_kg else None,
            'rounding': tariff.get_rounding_strategy_display() if tariff.rounding_strategy != 'NONE' else '',
        })
    return block


def build_order_snapshot(order, *, event_type: str, booking_payload: dict | None = None,
                         extra: dict | None = None) -> dict:
    """Return the JSON-safe payload for ``event_type`` about ``order``.

    ``booking_payload`` is the NEW_ORDER payload, when one exists, so update
    events repeat booking-time facts (e.g. the per-kg tariff) rather than
    today's values.
    """
    from ordering.models import Order
    from payments.models import Payment

    order = (
        Order.objects.select_related('user', 'laundry', 'coupon')
        .get(pk=order.pk)
    )
    payment = Payment.objects.filter(order_id=order.pk).first()
    user = order.user
    laundry = order.laundry

    items = []
    for line in order.items.select_related('service_type').order_by('name', 'id'):
        unit = _dec(line.price)
        variant = (line.service_type.name if line.service_type_id and line.service_type else '').strip()
        items.append({
            'name': line.name,
            # Service variant (e.g. "Wash & Iron"), frozen here at booking.
            'variant': variant if variant.lower() not in line.name.lower() else '',
            'quantity': int(line.quantity or 0),
            'unit_price': money(unit),
            'line_total': money(unit * int(line.quantity or 0)),
        })

    awaiting_quote = order.pricing_mode == 'CUSTOM_QUOTE' and order.priced_at is None
    total = _dec(order.total_amount)
    paid = order.payment_status == 'PAID'
    money_block = {
        'currency': order.currency or 'GHS',
        'items_total': money(order.items_total),
        'pickup_fee': money(order.pickup_fee),
        'delivery_fee': money(order.delivery_fee),
        'logistics_discount': money(order.logistics_discount),
        'promo_name': order.promo_name or '',
        'discount': money(order.discount_amount),
        'coupon_code': order.coupon.code if order.coupon_id and order.coupon else '',
        'tax': money(order.tax_amount),
        'platform_fee': money(order.platform_fee),
        'total': money(total),
        'amount_paid': money(total if paid else ZERO),
        'amount_due': money(ZERO if paid else total),
        # A by-weight total is the customer's estimate until the shop weighs.
        'is_estimate': order.pricing_mode == 'BY_WEIGHT',
        # Transport: what the customer pays in the app for pickup+delivery,
        # and what the rider is owed before any promo (frozen at booking).
        'transport_in_app': bool(order.delivery_fees_in_app),
        'transport_charged': money(_dec(order.pickup_fee) + _dec(order.delivery_fee)),
        'rider_cost': money(order.logistics_nominal_total),
        'pickup_distance_km': money(order.pickup_distance_km) if order.pickup_distance_km is not None else None,
        'delivery_distance_km': money(order.delivery_distance_km) if order.delivery_distance_km is not None else None,
        'awaiting_quote': awaiting_quote,
    }

    same_address = (
        (order.delivery_address or '').strip() == (order.pickup_address or '').strip()
        and order.delivery_lat == order.pickup_lat and order.delivery_lng == order.pickup_lng
    )
    pickup = _location(order.pickup_address or order.address, order.pickup_lat, order.pickup_lng)
    pickup['scheduled_at'] = _iso(order.pickup_date)
    pickup['window_end'] = _iso(_pickup_window_end(order))
    delivery = _location(order.delivery_address or order.address, order.delivery_lat, order.delivery_lng)
    delivery['scheduled_at'] = _iso(order.delivery_date)
    delivery['same_as_pickup'] = bool(same_address)

    full_name = (user.get_full_name() or '').strip()
    payload = {
        'v': PAYLOAD_VERSION,
        'event_type': event_type,
        'environment': conf.environment(),
        'snapshot_at': _iso(timezone.now()),
        'order': {
            'id': str(order.pk),
            'order_no': order.order_no,
            'status': order.status,
            'pricing_mode': order.pricing_mode,
            'created_at': _iso(order.created_at),
        },
        'customer': {
            'name': full_name,
            'phone': display_phone(user.phone) if user.phone else '',
            # Only when there is no phone, so ops still have a way to reach them.
            'email': '' if user.phone else (user.email or ''),
        },
        'laundry': {
            'id': str(laundry.pk),
            'name': laundry.name,
            'phone': display_phone(laundry.phone_number),
            'address': ', '.join(p for p in [(laundry.address or '').strip(), (laundry.city or '').strip()] if p),
        },
        'pickup': pickup,
        'delivery': delivery,
        'items': items,
        'weight': _weight_block(order, booking_payload),
        'money': money_block,
        'payment': _payment_block(order, payment),
        'notes': (order.special_instructions or '').strip(),
        'admin_url': admin_order_url(order.pk),
    }
    if extra:
        payload['event'] = extra
    return payload
