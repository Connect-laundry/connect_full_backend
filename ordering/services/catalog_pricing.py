"""Validated catalog contract and one batch pricing path for quote and checkout."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from django.conf import settings
from django.core import signing
from rest_framework.exceptions import ValidationError
from laundries.models.pricing import LaundryPricingItem

MONEY = Decimal('0.01')
RULE_KEYS = {'min_weight_kg', 'minimum_charge', 'rounding_strategy', 'weight_precision',
             'weight_min', 'weight_max', 'min_quantity', 'max_quantity',
             'inclusions', 'exclusions', 'package_description'}


def enabled():
    return bool(getattr(settings, 'UNIFIED_PRICING_ENABLED', False))


def number(value, label, *, minimum=Decimal('0'), maximum=Decimal('999999.99')):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({label: 'Enter a valid number.'})
    if not result.is_finite() or result < minimum or result > maximum:
        raise ValidationError({label: f'Enter a value between {minimum} and {maximum}.'})
    return result


def validate_offering(attrs, instance=None):
    def value(key, default=None):
        return attrs.get(key, getattr(instance, key, default))
    basis = value('pricing_basis', 'PER_ITEM')
    rules = value('pricing_rules', {})
    basket = value('basket')
    if not isinstance(rules, dict) or set(rules) - RULE_KEYS:
        raise ValidationError({'pricing_rules': 'Unsupported or invalid pricing rules.'})
    if value('currency', 'GHS') != 'GHS':
        raise ValidationError({'currency': 'Only GHS is supported; no currency conversion is performed.'})
    if basis != 'PER_ITEM' and not enabled():
        raise ValidationError({'pricing_basis': 'Advanced pricing is not enabled yet.'})
    price = value('unit_price', Decimal('0'))
    if basis != 'PER_ITEM' and number(price, 'unit_price') <= 0:
        raise ValidationError({'unit_price': 'A positive confirmed price is required.'})
    if basis == 'PER_BASKET':
        if not basket or not basket.is_active or not basket.capacity_description.strip() or not basket.overfill_policy.strip():
            raise ValidationError({'basket': 'Choose an active basket with a capacity and overfill policy.'})
        if instance and basket.laundry_id != instance.laundry_id:
            raise ValidationError({'basket': 'Basket and service must belong to the same laundry.'})
    elif basket:
        raise ValidationError({'basket': 'Only basket pricing can reference a basket.'})
    weight_keys = {'min_weight_kg', 'minimum_charge', 'rounding_strategy', 'weight_precision'}
    if basis != 'PER_KG' and weight_keys.intersection(rules):
        raise ValidationError({'pricing_rules': 'Kilogram rules only apply to per-kilogram pricing.'})
    if basis != 'WEIGHT_TIER' and {'weight_min', 'weight_max'}.intersection(rules):
        raise ValidationError({'pricing_rules': 'Interval boundaries only apply to flat weight tiers.'})
    for key in ('min_weight_kg', 'minimum_charge', 'weight_min', 'weight_max'):
        if key in rules:
            number(rules[key], key)
    if basis == 'PER_KG':
        if rules.get('rounding_strategy', 'NONE') not in {'NONE', 'UP_0_5_KG', 'UP_1_KG'}:
            raise ValidationError({'pricing_rules': 'Choose an explicit supported rounding strategy.'})
        if str(rules.get('weight_precision', '0.01')) not in {'0.01', '0.1', '0.10', '1', '1.0', '1.00'}:
            raise ValidationError({'pricing_rules': 'Weight precision must be 0.01, 0.1 or 1 kg.'})
    if basis == 'WEIGHT_TIER':
        if 'weight_min' not in rules or 'weight_max' not in rules or number(rules['weight_min'], 'weight_min') >= number(rules['weight_max'], 'weight_max'):
            raise ValidationError({'pricing_rules': 'Provide weight_min < weight_max for this flat interval.'})
    for key in ('min_quantity', 'max_quantity'):
        if key in rules:
            v = number(rules[key], key, minimum=Decimal('1'), maximum=Decimal('99'))
            if v != v.to_integral_value():
                raise ValidationError({'pricing_rules': 'Quantity limits must be whole numbers.'})
    if number(rules.get('min_quantity', 1), 'min_quantity') > number(rules.get('max_quantity', 99), 'max_quantity'):
        raise ValidationError({'pricing_rules': 'Minimum quantity exceeds maximum quantity.'})
    if 'package_description' in rules and (not isinstance(rules['package_description'], str) or len(rules['package_description']) > 2000):
        raise ValidationError({'pricing_rules': 'Package description must be text of at most 2000 characters.'})
    if basis == 'FIXED_PACKAGE' and not str(rules.get('package_description', '')).strip():
        raise ValidationError({'pricing_rules': 'Explain what this fixed package includes.'})
    for key in ('inclusions', 'exclusions'):
        if key in rules and (not isinstance(rules[key], list) or len(rules[key]) > 50 or any(not isinstance(v, str) or len(v) > 500 for v in rules[key])):
            raise ValidationError({'pricing_rules': f'{key} must be a list of short descriptions.'})
    return attrs


def basket_snapshot(basket):
    if not basket:
        return None
    return {key: str(getattr(basket, key)) if key in {'id', 'max_weight_kg'} and getattr(basket, key) is not None else getattr(basket, key)
            for key in ('id', 'name', 'description', 'capacity_description', 'max_weight_kg', 'max_pieces',
                        'dimensions', 'inclusions', 'exclusions', 'overfill_policy', 'container_requirements', 'revision')}


def price_lines(laundry, rows, *, contract=1, lock=False, check_revisions=True):
    """Two catalog queries regardless of line count; no client-supplied amounts."""
    from uuid import UUID
    from laundries.models.service import LaundryService
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ValidationError({'items': 'Choose between 1 and 100 service lines.'})
    ids = []
    for row in rows:
        try:
            ids.append(UUID(str(row['item'])))
        except (ValueError, TypeError, KeyError):
            raise ValidationError({'items': 'Choose valid catalog items.'})
    offerings = LaundryPricingItem.objects.filter(laundry=laundry, id__in=ids, is_active=True).select_related('basket')
    services = LaundryService.objects.filter(laundry=laundry, item_id__in=ids, is_available=True).select_related('item', 'service_type')
    if lock:
        # PostgreSQL cannot lock the nullable side of the basket outer join.
        offerings = offerings.select_for_update(of=('self',))
        services = services.select_for_update(of=('self',))
    by_id = {str(o.id): o for o in offerings}
    legacy = {(str(s.item_id), str(s.service_type_id)): s for s in services}
    output = []
    seen = set()
    for row, item_id in zip(rows, ids):
        key = (str(item_id), str(row.get('service_type', '')))
        service = legacy.get(key)
        offering = by_id.get(key[0])
        identity = key if service else (key[0], '')
        if identity in seen:
            raise ValidationError({'items': 'Combine repeated services into one line.'})
        seen.add(identity)
        quantity = number(row.get('quantity', 1), 'quantity', minimum=Decimal('1'), maximum=Decimal('99'))
        if quantity != quantity.to_integral_value():
            raise ValidationError({'quantity': 'Enter a whole number of items, baskets or packages.'})
        quantity = int(quantity)
        if service:
            if row.get('weight_kg') is not None or row.get('piece_count') is not None:
                raise ValidationError({'items': 'Weight and piece count cannot be attached to a per-item service.'})
            unit = service.price
            snapshot = {'offering_id': str(service.id), 'name': service.item.name, 'service_type': service.service_type.name,
                        'pricing_basis': 'PER_ITEM', 'currency': 'GHS', 'unit_price': str(unit), 'quantity': quantity}
            output.append({'item': service.item, 'service_type': service.service_type, 'name': service.item.name,
                           'quantity': quantity, 'price': unit, 'pricing_snapshot': snapshot})
            continue
        if not offering:
            raise ValidationError({'items': 'A selected service is unavailable. Refresh this laundry.'})
        advanced = offering.pricing_basis != 'PER_ITEM'
        if advanced and (str(contract) != '2' or not enabled()):
            raise ValidationError({'code': 'PRICING_UPGRADE_REQUIRED', 'items': 'Update the app to order this service.'})
        if str(contract) == '2' and check_revisions:
            if row.get('revision') is None or str(row['revision']) != str(offering.revision):
                raise ValidationError({'code': 'STALE_QUOTE', 'items': 'These prices have changed. Refresh and review your selection.'})
        rules = offering.pricing_rules
        validate_offering({}, offering)
        if quantity < int(rules.get('min_quantity', 1)) or quantity > int(rules.get('max_quantity', 99)):
            raise ValidationError({'quantity': 'Quantity is outside this service\'s limits.'})
        unit = offering.unit_price
        weight = None
        billable = None
        if offering.pricing_basis in {'PER_KG', 'WEIGHT_TIER'}:
            if row.get('piece_count') is not None:
                raise ValidationError({'piece_count': 'Piece count only applies to basket pricing.'})
            if quantity != 1:
                raise ValidationError({'quantity': 'Weight lines use quantity 1 and a weight_kg value.'})
            weight = number(row.get('weight_kg'), 'weight_kg', minimum=MONEY, maximum=Decimal('9999.99'))
            precision = Decimal(str(rules.get('weight_precision', '0.01')))
            if weight % precision:
                raise ValidationError({'weight_kg': f'Enter weight in increments of {precision} kg.'})
            if offering.pricing_basis == 'PER_KG':
                if weight < Decimal(str(rules.get('min_weight_kg', 0))):
                    raise ValidationError({'weight_kg': 'Weight is below the laundry\'s stated minimum.'})
                billable = weight
                strategy = rules.get('rounding_strategy', 'NONE')
                step = {'UP_0_5_KG': Decimal('0.5'), 'UP_1_KG': Decimal('1')}.get(strategy)
                if step:
                    billable = (weight / step).to_integral_value(rounding=ROUND_CEILING) * step
                unit = max(offering.unit_price * billable, Decimal(str(rules.get('minimum_charge', 0)))).quantize(MONEY, rounding=ROUND_HALF_UP)
            else:
                if not (Decimal(str(rules['weight_min'])) < weight <= Decimal(str(rules['weight_max']))):
                    raise ValidationError({'weight_kg': 'Weight is outside this flat interval.'})
        elif offering.pricing_basis == 'PER_BASKET':
            if row.get('weight_kg') is not None:
                weight = number(row.get('weight_kg'), 'weight_kg', minimum=MONEY, maximum=Decimal('9999.99'))
                if offering.basket.max_weight_kg is not None and weight > offering.basket.max_weight_kg:
                    raise ValidationError({'weight_kg': 'This load exceeds the basket maximum weight.'})
            piece_count = row.get('piece_count')
            if piece_count is not None:
                piece_count = number(piece_count, 'piece_count', minimum=Decimal('1'), maximum=Decimal('9999'))
                if piece_count != piece_count.to_integral_value():
                    raise ValidationError({'piece_count': 'Piece count must be a whole number.'})
                if offering.basket.max_pieces is not None and piece_count > offering.basket.max_pieces:
                    raise ValidationError({'piece_count': 'This load exceeds the basket piece limit.'})
        elif row.get('weight_kg') is not None or row.get('piece_count') is not None:
            raise ValidationError({'items': 'Weight and piece count only apply to basket or weight pricing.'})
        line_total = (unit * quantity).quantize(MONEY, rounding=ROUND_HALF_UP)
        if line_total > Decimal('999999.99'):
            raise ValidationError({'items': 'This line exceeds the maximum order amount.'})
        snapshot = {'offering_id': str(offering.id), 'name': offering.item_name, 'description': offering.description,
                    'service_type': offering.service_type, 'pricing_basis': offering.pricing_basis, 'currency': offering.currency,
                    'unit_price': str(offering.unit_price), 'quantity': quantity, 'revision': offering.revision,
                    'pricing_rules': rules, 'basket': basket_snapshot(offering.basket), 'weight_kg': str(weight) if weight else None,
                    'piece_count': int(piece_count) if offering.pricing_basis == 'PER_BASKET' and piece_count else None,
                    'billable_weight_kg': str(billable) if billable else None, 'weight_status': 'ESTIMATED' if weight else None,
                    'line_total': str(line_total)}
        output.append({'item': None, 'service_type': None, 'name': offering.item_name[:100], 'quantity': quantity,
                       'price': unit, 'pricing_snapshot': snapshot})
    total = sum((line['price'] * line['quantity'] for line in output), Decimal('0.00'))
    if total > Decimal('999999.99'):
        raise ValidationError({'items': 'Order exceeds the maximum amount.'})
    return output, total


def quote_payload(laundry, user, lines, total, breakdown=None):
    # Bind each financial component, not just a total that could stay unchanged
    # when a tax, transport charge or discount changes.
    money_keys = ('items_total', 'pickup_fee', 'delivery_fee', 'tax', 'platform_fee', 'discount')
    return {'laundry': str(laundry.pk), 'user': str(user.pk), 'lines': [l['pricing_snapshot'] for l in lines],
            'total': str(total), 'amounts': {key: str((breakdown or {}).get(key, '0.00')) for key in money_keys}}


def make_quote(laundry, user, lines, total, breakdown=None):
    payload = quote_payload(laundry, user, lines, total, breakdown)
    return signing.dumps(payload, salt='catalog-quote-v2', compress=True)


def check_quote(token, laundry, user, lines, total, breakdown=None):
    try:
        payload = signing.loads(token, salt='catalog-quote-v2', max_age=getattr(settings, 'PRICING_QUOTE_TTL_SECONDS', 900))
    except (signing.BadSignature, TypeError):
        raise ValidationError({'code': 'STALE_QUOTE', 'quote_token': 'Your quote expired. Review a fresh estimate.'})
    expected = quote_payload(laundry, user, lines, total, breakdown)
    if payload != expected:
        raise ValidationError({'code': 'STALE_QUOTE', 'quote_token': 'Your selection or price changed. Review a fresh estimate.'})
