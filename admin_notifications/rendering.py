"""NotificationRenderer: turns a frozen event payload into SMS and WhatsApp messages.

Rendering is a pure function of the payload, so a retry sends exactly what the
first attempt would have sent and a preview in Django admin matches the real
message.

Channel constraints that shape this module:

- WhatsApp business-initiated messages must use an approved template. Meta
  rejects template parameters containing newlines, tabs or runs of spaces,
  and a hydrated body over 1024 characters. Long fields (item lists, notes,
  addresses) therefore move into labelled continuation messages instead of
  being cut.
- SMS text is kept GSM-7 friendly (no emoji, "GHS" not the cedi sign) so a
  segment holds 153-160 characters rather than 67-70. Arkesel concatenates
  long messages itself; beyond ``ADMIN_SMS_MAX_CHARS_PER_MESSAGE`` the text is
  split into parts labelled "(1/3)", "(2/3)", ... Nothing is silently dropped.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from . import conf

try:
    from zoneinfo import ZoneInfo
    ACCRA = ZoneInfo('Africa/Accra')
except Exception:  # pragma: no cover - tzdata ships in requirements.txt
    ACCRA = dt_timezone.utc

# Exact template bodies to submit to Meta (category UTILITY, language en).
# The code relies on the variable order below; keep both in sync.
NEW_ORDER_TEMPLATE_NAME = 'simame_admin_new_order_v1'
NEW_ORDER_TEMPLATE_BODY = (
    'SIMAME NEW ORDER {{1}}\n'
    'Booked: {{2}}\n'
    '\n'
    'Customer: {{3}}\n'
    'Laundry: {{4}}\n'
    'Payment: {{5}}\n'
    '\n'
    'Pickup: {{6}}\n'
    'Delivery: {{7}}\n'
    '\n'
    'Items: {{8}}\n'
    'Amounts: {{9}}\n'
    'Notes: {{10}}\n'
    '\n'
    'Admin (login required): {{11}}\n'
    'Please coordinate the rider for this order.'
)
ORDER_UPDATE_TEMPLATE_NAME = 'simame_admin_order_update_v1'
ORDER_UPDATE_TEMPLATE_BODY = (
    'SIMAME ORDER UPDATE\n'
    'Update: {{1}}\n'
    'Order: {{2}}\n'
    'Details: {{3}}\n'
    'Admin (login required): {{4}}\n'
    'This is an automated Simame operations alert.'
)
SEE_NEXT = 'See next message'


@dataclass(frozen=True)
class WhatsAppPart:
    template_key: str  # 'new_order' | 'order_update'
    params: tuple[str, ...]

    @property
    def body(self) -> str:
        template = NEW_ORDER_TEMPLATE_BODY if self.template_key == 'new_order' else ORDER_UPDATE_TEMPLATE_BODY
        return hydrate(template, self.params)


# --- formatting helpers ----------------------------------------------------

def _parse(iso):
    if not iso:
        return None
    try:
        value = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(ACCRA)


def fmt_time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def fmt_dt(iso) -> str:
    """'Sat 27 Sep 2026, 3:22 PM GMT' in Africa/Accra, whatever the server zone."""
    value = _parse(iso)
    if value is None:
        return ''
    return f"{value.strftime('%a')} {value.day} {value.strftime('%b %Y')}, {fmt_time(value)} GMT"


def fmt_window(start_iso, end_iso) -> str:
    start = _parse(start_iso)
    if start is None:
        return 'Not scheduled'
    end = _parse(end_iso)
    text = f"{start.strftime('%a')} {start.day} {start.strftime('%b %Y')}, {fmt_time(start)}"
    if end and end > start:
        if end.date() == start.date():
            text += f' - {fmt_time(end)}'
        else:
            text += f" - {end.strftime('%a')} {end.day} {end.strftime('%b')}, {fmt_time(end)}"
    return text + ' GMT'


def ghs(amount, currency='GHS') -> str:
    return f'{currency or "GHS"} {amount}'


def map_link(lat, lng) -> str:
    """Google Maps URL from stored coordinates only. No API key involved."""
    if lat is None or lng is None:
        return ''
    return f'https://www.google.com/maps/search/?api=1&query={lat},{lng}'


def hydrate(template: str, params) -> str:
    text = template
    for index in range(len(params), 0, -1):  # {{10}} before {{1}}
        text = text.replace('{{%d}}' % index, params[index - 1])
    return text


def wa_param(value) -> str:
    """Make a value legal as a WhatsApp template parameter."""
    text = str(value or '').replace('\r', ' ').replace('\t', ' ')
    text = re.sub(r'\s*\n\s*', ' | ', text)
    text = re.sub(r' {2,}', ' ', text).strip()
    return text or '-'


_SMS_REPLACEMENTS = {
    '₵': 'GHS', '¢': 'GHS',  # cedi / cent signs
    '‘': "'", '’': "'", '‚': "'", '“': '"', '”': '"', '„': '"',
    '–': '-', '—': '-', '−': '-', '…': '...', ' ': ' ', '•': '-',
    '·': '-', '→': '->',
}
_GSM_BASIC = set(
    '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ'
    'ÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    'ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà'
)
_GSM_EXTENDED = set('^{}\\[~]|€\f')


def to_sms_text(text: str) -> str:
    """GSM-7 friendly text: no emoji or decorative symbols, accents folded."""
    out = []
    for char in str(text or ''):
        char = _SMS_REPLACEMENTS.get(char, char)
        if len(char) > 1 or char in _GSM_BASIC or char in _GSM_EXTENDED:
            out.append(char)
            continue
        if unicodedata.category(char) in ('So', 'Sk', 'Cs', 'Co', 'Cn') or ord(char) > 0xFFFF:
            continue  # emoji and pictographs carry no operational information
        folded = unicodedata.normalize('NFKD', char).encode('ascii', 'ignore').decode('ascii')
        # Characters with no ASCII form (e.g. non-Latin names) are kept: the
        # message falls back to UCS-2, but a name is never mangled.
        out.append(folded or char)
    return re.sub(r'[ \t]+\n', '\n', ''.join(out))


def sms_segments(text: str) -> int:
    """Billable SMS segments for ``text`` (GSM-7 or UCS-2 concatenation rules)."""
    if not text:
        return 0
    if all(c in _GSM_BASIC or c in _GSM_EXTENDED for c in text):
        units = sum(2 if c in _GSM_EXTENDED else 1 for c in text)
        return 1 if units <= 160 else math.ceil(units / 153)
    units = sum(2 if ord(c) > 0xFFFF else 1 for c in text)
    return 1 if units <= 70 else math.ceil(units / 67)


# --- shared field text -------------------------------------------------------

def _money(p):
    return p.get('money') or {}


def _cur(p):
    return _money(p).get('currency') or 'GHS'


def order_ref(p) -> str:
    return conf_prefix(p) + (p.get('order') or {}).get('order_no', '')


def conf_prefix(p) -> str:
    env = (p.get('environment') or '').lower()
    return '' if env == conf.PRODUCTION else f'[{(env or "development").upper()}] '


def customer_text(p) -> str:
    c = p.get('customer') or {}
    parts = [c.get('name') or 'Name not on profile']
    parts.append(c.get('phone') or (f"no phone, email {c['email']}" if c.get('email') else 'no phone on profile'))
    return ', '.join(parts)


def laundry_text(p) -> str:
    l = p.get('laundry') or {}
    parts = [l.get('name') or '-']
    if l.get('phone'):
        parts.append(l['phone'])
    if l.get('address'):
        parts.append(l['address'])
    return ', '.join(parts)


def payment_lines(p) -> list[str]:
    pay = p.get('payment') or {}
    m = _money(p)
    lines = [f"Payment method: {pay.get('method_label', '-')}", f"Payment status: {pay.get('status_label', '-')}"]
    if pay.get('is_cod') and pay.get('status') == 'NOT_YET_PAID':
        if m.get('awaiting_quote'):
            lines.append('Amount to collect: set by the laundry quote (not priced yet)')
        elif m.get('is_estimate'):
            lines.append(f"Amount to collect: about {ghs(m.get('amount_due'), _cur(p))} (ESTIMATE, final after weigh-in)")
        else:
            lines.append(f"Amount to collect: {ghs(m.get('amount_due'), _cur(p))}")
    elif pay.get('status') == 'PENDING':
        lines.append('Wait for PAYMENT CONFIRMED before dispatching a rider')
    if pay.get('reference') and pay.get('status') == 'PAID':
        lines.append(f"Ref: {pay['reference']}")
    return lines


def payment_summary(p) -> str:
    """One-line payment text for the WhatsApp template ("Payment: ...")."""
    lines = payment_lines(p)
    lines[0] = lines[0].removeprefix('Payment method: ')
    lines[1] = lines[1].removeprefix('Payment status: ')
    return ' - '.join(lines)


def pickup_lines(p) -> list[str]:
    pk = p.get('pickup') or {}
    lines = [fmt_window(pk.get('scheduled_at'), pk.get('window_end')), pk.get('address') or 'No address stored']
    link = map_link(pk.get('lat'), pk.get('lng'))
    lines.append(f'Map: {link}' if link else 'Map: no coordinates stored')
    return lines


def delivery_lines(p) -> list[str]:
    d = p.get('delivery') or {}
    when = fmt_window(d.get('scheduled_at'), None) if d.get('scheduled_at') else 'Not scheduled yet (arrange with laundry)'
    if d.get('same_as_pickup'):
        return [when, 'Same address as pickup']
    lines = [when, d.get('address') or 'No address stored']
    link = map_link(d.get('lat'), d.get('lng'))
    lines.append(f'Map: {link}' if link else 'Map: no coordinates stored')
    return lines


def item_lines(p) -> list[str]:
    mode = (p.get('order') or {}).get('pricing_mode', '')
    cur = _cur(p)
    m = _money(p)
    lines = []
    if mode == 'CUSTOM_QUOTE' and m.get('awaiting_quote'):
        return ['PAY AFTER QUOTE - laundry weighs/inspects and sends the price']
    weight = p.get('weight')
    if mode == 'BY_WEIGHT' and weight:
        lines.append('Pricing: BY WEIGHT')
        if weight.get('rate_per_kg'):
            lines.append(f"Rate: {ghs(weight['rate_per_kg'], cur)}/kg")
        if weight.get('estimated_kg'):
            lines.append(f"Estimated weight: {weight['estimated_kg']} kg")
        extras = []
        if weight.get('minimum_weight_kg'):
            extras.append(f"min {weight['minimum_weight_kg']} kg")
        if weight.get('minimum_charge'):
            extras.append(f"min charge {ghs(weight['minimum_charge'], cur)}")
        if weight.get('rounding'):
            extras.append(weight['rounding'].lower())
        if extras:
            lines.append('Tariff: ' + ', '.join(extras))
        lines.append(f"Estimated charge: {ghs(m.get('items_total'), cur)}")
        lines.append('Final charge: NOT CONFIRMED (set after the laundry weighs)')
        return lines
    for item in p.get('items') or []:
        name = f"{item['name']} ({item['variant']})" if item.get('variant') else item['name']
        if int(item.get('quantity') or 0) == 1:
            lines.append(f"1 x {name} = {ghs(item['line_total'], cur)}")
        else:
            lines.append(
                f"{item['quantity']} x {name} @ {ghs(item['unit_price'], cur)} = {ghs(item['line_total'], cur)}"
            )
    return lines or ['No items']


def amount_lines(p) -> list[str]:
    m = _money(p)
    cur = _cur(p)
    if m.get('awaiting_quote'):
        return ['No price yet (awaiting laundry quote)']
    lines = [f"{'Est. laundry subtotal' if m.get('is_estimate') else 'Subtotal'}: {ghs(m.get('items_total'), cur)}"]
    lines.extend(transport_lines(p))
    if m.get('logistics_discount') and m['logistics_discount'] != '0.00':
        promo = f" ({m['promo_name']})" if m.get('promo_name') else ''
        lines.append(f"Transport promo{promo}: -{ghs(m['logistics_discount'], cur)}")
    if m.get('discount') and m['discount'] != '0.00':
        code = f" ({m['coupon_code']})" if m.get('coupon_code') else ''
        lines.append(f"Discount{code}: -{ghs(m['discount'], cur)}")
    if m.get('tax') and m['tax'] != '0.00':
        lines.append(f"Tax: {ghs(m['tax'], cur)}")
    if m.get('platform_fee') and m['platform_fee'] != '0.00':
        lines.append(f"Service fee: {ghs(m['platform_fee'], cur)}")
    label = 'ESTIMATED TOTAL' if m.get('is_estimate') else 'TOTAL'
    lines.append(f"{label}: {ghs(m.get('total'), cur)}")
    lines.append(f"Paid: {ghs(m.get('amount_paid') or '0.00', cur)}")
    outstanding = f"Outstanding: {ghs(m.get('amount_due') or '0.00', cur)}"
    lines.append(outstanding + (' (estimate)' if m.get('is_estimate') and m.get('amount_due') != '0.00' else ''))
    return lines


def transport_lines(p) -> list[str]:
    """What the customer pays for transport and what the rider is owed."""
    m = _money(p)
    cur = _cur(p)
    lines = []
    if m.get('transport_in_app'):
        lines.append(f"Transport charged: {ghs(m.get('transport_charged') or '0.00', cur)} "
                     f"(pickup {ghs(m.get('pickup_fee') or '0.00', cur)}, "
                     f"delivery {ghs(m.get('delivery_fee') or '0.00', cur)})")
    else:
        lines.append('Transport: not billed in the app')
    if m.get('rider_cost') and m['rider_cost'] != '0.00':
        lines.append(f"Rider cost: {ghs(m['rider_cost'], cur)}")
    return lines


def notes_text(p) -> str:
    return p.get('notes') or 'None'


# --- event-specific update content --------------------------------------------

_HEADLINES = {
    'PAYMENT_CONFIRMED': 'PAYMENT CONFIRMED',
    'ORDER_CANCELLED': 'ORDER CANCELLED - DO NOT DISPATCH A RIDER',
    'ORDER_REJECTED': 'ORDER REJECTED BY LAUNDRY - DO NOT DISPATCH A RIDER',
    'ORDER_RESCHEDULED': 'UPDATED ORDER - DO NOT USE PREVIOUS SCHEDULE',
    'PICKUP_LOCATION_CHANGED': 'UPDATED ORDER - NEW PICKUP LOCATION',
    'DELIVERY_LOCATION_CHANGED': 'UPDATED ORDER - NEW DELIVERY LOCATION',
    'ORDER_PRICE_FINALIZED': 'ORDER PRICE FINALIZED',
}


def headline(p) -> str:
    event_type = p.get('event_type', '')
    if event_type == 'PAYMENT_CONFIRMED' and (p.get('payment') or {}).get('is_cod'):
        return 'CASH COLLECTED'
    return _HEADLINES.get(event_type, event_type.replace('_', ' '))


def update_detail_lines(p) -> list[str]:
    """Ordered detail lines for every event type other than NEW_ORDER."""
    event_type = p.get('event_type', '')
    ev = p.get('event') or {}
    cur = _cur(p)
    lines: list[str] = []
    if event_type == 'PAYMENT_CONFIRMED':
        lines.append(f"Amount: {ghs(ev.get('amount') or _money(p).get('total'), cur)}")
        pay = p.get('payment') or {}
        method = pay.get('method_label', '')
        if pay.get('channel') and not pay.get('is_cod'):
            method = f"{pay['channel']} via Paystack"
        lines.append(f'Method: {method}')
        if pay.get('reference'):
            lines.append(f"Reference: {pay['reference']}")
        if pay.get('paid_at'):
            lines.append(f"Paid at: {fmt_dt(pay['paid_at'])}")
    elif event_type in ('ORDER_CANCELLED', 'ORDER_REJECTED'):
        if ev.get('at'):
            lines.append(f"{'Cancelled' if event_type == 'ORDER_CANCELLED' else 'Rejected'} at: {fmt_dt(ev['at'])}")
        if ev.get('from_status'):
            lines.append(f"Previous status: {ev['from_status']}")
        if ev.get('reason'):
            lines.append(f"Reason: {ev['reason']}")
        pk = p.get('pickup') or {}
        lines.append(
            f"Pickup was: {fmt_window(pk.get('scheduled_at'), pk.get('window_end'))}, {pk.get('address') or '-'}"
        )
        pay = p.get('payment') or {}
        lines.append(f"Payment: {pay.get('method_label', '-')} - {pay.get('status_label', '-')}")
    elif event_type in ('ORDER_RESCHEDULED', 'PICKUP_LOCATION_CHANGED', 'DELIVERY_LOCATION_CHANGED'):
        for change in ev.get('changes') or []:
            lines.append(f"{change['label']}: {change['old'] or '-'} -> {change['new'] or '-'}")
        pk = p.get('pickup') or {}
        d = p.get('delivery') or {}
        if event_type in ('ORDER_RESCHEDULED', 'PICKUP_LOCATION_CHANGED'):
            link = map_link(pk.get('lat'), pk.get('lng'))
            lines.append(f"Current pickup: {fmt_window(pk.get('scheduled_at'), pk.get('window_end'))}, {pk.get('address') or '-'}")
            if link:
                lines.append(f'Pickup map: {link}')
        if event_type in ('ORDER_RESCHEDULED', 'DELIVERY_LOCATION_CHANGED'):
            lines.extend(['Current delivery: ' + ', '.join(delivery_lines(p)[:2])])
            link = map_link(d.get('lat'), d.get('lng'))
            if link and not d.get('same_as_pickup'):
                lines.append(f'Delivery map: {link}')
    elif event_type == 'ORDER_PRICE_FINALIZED':
        if ev.get('previous_total') is not None:
            lines.append(f"Previous total: {ghs(ev['previous_total'], cur)}")
        lines.extend(item_lines(p))
        lines.extend(amount_lines(p))
        pay = p.get('payment') or {}
        lines.append(f"Payment: {pay.get('method_label', '-')} - {pay.get('status_label', '-')}")
    lines.append(f'Customer: {customer_text(p)}')
    lines.append(f"Laundry: {(p.get('laundry') or {}).get('name', '-')}")
    return lines


# --- SMS -------------------------------------------------------------------------

def full_text(p) -> str:
    """The complete operational message, before any channel-specific encoding."""
    event_type = p.get('event_type', '')
    ref = (p.get('order') or {}).get('order_no', '')
    if event_type == 'NEW_ORDER':
        order = p.get('order') or {}
        sections = [
            [f'SIMAME NEW ORDER {ref}', f"Booked: {fmt_dt(order.get('created_at'))}"],
            ['CUSTOMER', customer_text(p)],
            ['LAUNDRY', laundry_text(p)],
            ['PAYMENT', *payment_lines(p)],
            ['PICKUP', *pickup_lines(p)],
            ['DELIVERY', *delivery_lines(p)],
            ['ITEMS', *item_lines(p)],
            amount_lines(p),
            [f'Notes: {notes_text(p)}'],
            [f"Admin: {p.get('admin_url', '')}"],
        ]
    else:
        sections = [
            [f'SIMAME {headline(p)}', f'Order: {ref}'],
            update_detail_lines(p),
            [f"Admin: {p.get('admin_url', '')}"],
        ]
    return conf_prefix(p) + '\n\n'.join('\n'.join(s) for s in sections if s)


def sms_full_text(p) -> str:
    return to_sms_text(full_text(p))


def sms_compact_text(p) -> str:
    ref = (p.get('order') or {}).get('order_no', '')
    pay = p.get('payment') or {}
    pk = p.get('pickup') or {}
    m = _money(p)
    if p.get('event_type') == 'NEW_ORDER':
        lines = [
            f'SIMAME NEW ORDER {ref}',
            customer_text(p),
            f"Laundry: {(p.get('laundry') or {}).get('name', '-')}",
            f"Pay: {pay.get('method_label', '-')} / {pay.get('status_label', '-')}",
            f"Pickup: {fmt_window(pk.get('scheduled_at'), pk.get('window_end'))}",
            pk.get('address') or '-',
            f"Total: {ghs(m.get('total'), _cur(p))}{' (est.)' if m.get('is_estimate') else ''}",
            f"Admin: {p.get('admin_url', '')}",
        ]
    else:
        lines = [f'SIMAME {headline(p)}', f'Order: {ref}', *update_detail_lines(p)[:3],
                 f"Admin: {p.get('admin_url', '')}"]
    return to_sms_text(conf_prefix(p) + '\n'.join(lines))


_CURRENCY_PAIR = re.compile(r'\b(GHS|GH|USD) (-?\d)')
_JOIN = '\x1f'  # unit separator: never present in message text


def _tokens(line: str) -> list[str]:
    """Words, keeping "GHS 20.00" together so a price is never split."""
    joined = _CURRENCY_PAIR.sub(lambda m: f'{m.group(1)}{_JOIN}{m.group(2)}', line)
    return [token.replace(_JOIN, ' ') for token in joined.split(' ')]


def _wrap(line: str, width: int) -> list[str]:
    if len(line) <= width:
        return [line]
    out, current = [], ''
    for word in _tokens(line):
        while len(word) > width:  # a single unbreakable token (e.g. a URL)
            if current:
                out.append(current)
                current = ''
            out.append(word[:width])
            word = word[width:]
        candidate = f'{current} {word}' if current else word
        if len(candidate) > width:
            out.append(current)
            current = word
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def split_sms(text: str, order_no: str, max_chars: int, prefix: str = '') -> list[str]:
    if len(text) <= max_chars:
        return [text]
    header_room = len(f'{prefix}SIMAME ORDER {order_no} (99/99)\n')
    width = max_chars - header_room
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split('\n'):
        for piece in _wrap(line, width) if line else ['']:
            added = len(piece) + (1 if current else 0)
            if current and size + added > width:
                chunks.append('\n'.join(current).strip('\n'))
                current, size = [], 0
                added = len(piece)
            current.append(piece)
            size += added
    if current and '\n'.join(current).strip():
        chunks.append('\n'.join(current).strip('\n'))
    total = len(chunks)
    return [f'{prefix}SIMAME ORDER {order_no} ({i}/{total})\n{chunk}' for i, chunk in enumerate(chunks, 1)]


def render_sms_parts(p) -> list[str]:
    text = sms_full_text(p) if conf.sms_detail_mode() == 'full' else sms_compact_text(p)
    order_no = (p.get('order') or {}).get('order_no', '')
    return split_sms(text, order_no, conf.sms_max_chars(), prefix=to_sms_text(conf_prefix(p)))


# --- WhatsApp --------------------------------------------------------------------

def _chunk_segments(segments: list[str], limit: int) -> list[str]:
    chunks: list[str] = []
    current = ''
    for segment in segments:
        for piece in _wrap(wa_param(segment), limit):
            candidate = f'{current} | {piece}' if current else piece
            if len(candidate) > limit and current:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _update_parts(p, headline_text: str, detail_segments: list[str], limit: int, first_index=1,
                  total_extra=0) -> list[WhatsAppPart]:
    ref = wa_param(order_ref(p))
    admin = wa_param(p.get('admin_url', ''))
    # Room left for {{3}} once the fixed text and the other variables are in.
    head_room = len(wa_param(headline_text)) + len(' (99/99)')
    fixed = len(hydrate(ORDER_UPDATE_TEMPLATE_BODY, ('x' * head_room, ref, '', admin)))
    detail_limit = max(120, limit - fixed)
    chunks = _chunk_segments(detail_segments, detail_limit) or ['-']
    total = len(chunks) + total_extra
    parts = []
    for offset, chunk in enumerate(chunks):
        number = first_index + offset
        title = headline_text if total == 1 else f'{headline_text} ({number}/{total})'
        parts.append(WhatsAppPart('order_update', (wa_param(title), ref, chunk, admin)))
    return parts


def render_whatsapp_parts(p) -> list[WhatsAppPart]:
    limit = conf.whatsapp_max_body_chars()
    if p.get('event_type') != 'NEW_ORDER':
        return _update_parts(p, headline(p), update_detail_lines(p), limit)

    order = p.get('order') or {}
    # Long fields keep their individual lines so a continuation message
    # breaks between items, never in the middle of one.
    sources = {
        5: ('Pickup', pickup_lines(p)),
        6: ('Delivery', delivery_lines(p)),
        7: ('Items', item_lines(p)),
        9: ('Notes', [notes_text(p)]),
    }
    params = [
        order_ref(p),
        fmt_dt(order.get('created_at')),
        customer_text(p),
        laundry_text(p),
        payment_summary(p),
        ' | '.join(sources[5][1]),
        ' | '.join(sources[6][1]),
        '; '.join(sources[7][1]),
        ' | '.join(amount_lines(p)),
        sources[9][1][0],
        p.get('admin_url', ''),
    ]
    params = [wa_param(v) for v in params]
    moved: list[int] = []
    while len(hydrate(NEW_ORDER_TEMPLATE_BODY, params)) > limit:
        movable = [i for i in sources if params[i] != SEE_NEXT]
        if not movable:
            break
        index = max(movable, key=lambda i: len(params[i]))
        moved.append(index)
        params[index] = SEE_NEXT
    first = WhatsAppPart('new_order', tuple(params))
    if not moved:
        return [first]
    segments: list[str] = []
    for index in sorted(moved):  # reading order: pickup, delivery, items, notes
        label, lines = sources[index]
        segments.append(f'{label}: {lines[0]}')
        segments.extend(lines[1:])
    rest = _update_parts(p, 'NEW ORDER DETAILS', segments, limit, first_index=2, total_extra=1)
    return [first, *rest]


# --- Telegram ----------------------------------------------------------------------

TELEGRAM_MAX_CHARS = 4000  # Telegram's cap is 4096; leave room for the part header


def render_telegram_parts(p) -> list[str]:
    """Full detail in Unicode (names, cedi sign and all). Split, at line
    boundaries only, just when an order exceeds one Telegram message."""
    order_no = (p.get('order') or {}).get('order_no', '')
    return split_sms(full_text(p), order_no, TELEGRAM_MAX_CHARS, prefix=conf_prefix(p))
