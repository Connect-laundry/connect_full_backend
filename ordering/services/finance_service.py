from datetime import datetime
from decimal import Decimal, ROUND_CEILING
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.db.models import Sum, F

class FinanceService:
    """
    Centralized service for handling all financial calculations.
    Ensures consistency across views and orders.
    """

    @staticmethod
    def delivery_fees_in_app():
        """
        Whether pickup and delivery are billed through the app.

        Decided only by the admin's LogisticsPricingConfig, so switching
        pricing on or off takes effect on the next quote with no redeploy
        and no app release.
        """
        from logistics.models import LogisticsPricingConfig
        config = LogisticsPricingConfig.get_active()
        return bool(config and config.pricing_enabled)


    @staticmethod
    def compute_weight_price(weight_pricing, weight_kg):
        """
        Price a by-weight order from a laundry's tariff.

        Applies the tariff's rounding, charges for at least the minimum order
        weight, and never returns less than the minimum charge. The result is
        the authoritative price: the client's estimate is only for display and
        is never trusted here.

        Raises ValueError when the inputs cannot yield a price, so the caller
        rejects the order rather than creating a free one.
        """
        if weight_pricing is None:
            raise ValueError('This laundry has no weight pricing configured.')

        try:
            weight = Decimal(str(weight_kg))
        except (TypeError, ValueError, ArithmeticError):
            raise ValueError('Estimated weight is not a valid number.')
        if weight <= 0:
            raise ValueError('Estimated weight must be greater than zero.')

        min_weight = weight_pricing.minimum_order_weight_kg
        if min_weight is not None and weight < Decimal(str(min_weight)):
            # The order is charged as if it were the minimum weight.
            weight = Decimal(str(min_weight))

        strategy = getattr(weight_pricing, 'rounding_strategy', 'NONE')
        if strategy == 'UP_0_5_KG':
            weight = (weight / Decimal('0.5')).to_integral_value(rounding=ROUND_CEILING) * Decimal('0.5')
        elif strategy == 'UP_1_KG':
            weight = weight.to_integral_value(rounding=ROUND_CEILING)

        rate = Decimal(str(weight_pricing.base_price_per_kg))
        minimum_charge = Decimal(str(weight_pricing.minimum_charge or '0'))

        total = (weight * rate).quantize(Decimal('0.01'))
        return max(total, minimum_charge.quantize(Decimal('0.01')))

    @staticmethod
    def calculate_tax_amount(amount, tax_rate=None):
        """Returns the tax amount for a given base amount."""
        if tax_rate is None:
            tax_rate = Decimal(str(settings.TAX_RATE))
        else:
            tax_rate = Decimal(str(tax_rate))
            
        return (Decimal(str(amount)) * tax_rate).quantize(Decimal('0.01'))

    @staticmethod
    def calculate_platform_fee(amount, fee_rate=None):
        """
        The platform's commission on a taxable amount.

        Zero by default: the platform takes no cut, so the customer pays the
        laundry's prices and nothing more. Defined once here because the
        estimate endpoint and the order breakdown both need it, and two copies
        of the same formula drift the moment a rate is introduced.
        """
        if fee_rate is None:
            fee_rate = Decimal(str(getattr(settings, 'PLATFORM_FEE_RATE', 0)))
        else:
            fee_rate = Decimal(str(fee_rate))

        return (Decimal(str(amount)) * fee_rate).quantize(Decimal('0.01'))

    @staticmethod
    def calculate_haversine_distance(lat1, lon1, lat2, lon2):
        import math
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return None
        
        # Radius of the Earth in km
        R = 6371.0
        
        try:
            phi1 = math.radians(float(lat1))
            phi2 = math.radians(float(lat2))
            delta_phi = math.radians(float(lat2) - float(lat1))
            delta_lambda = math.radians(float(lon2) - float(lon1))
            
            a = math.sin(delta_phi / 2.0)**2 + \
                math.cos(phi1) * math.cos(phi2) * \
                math.sin(delta_lambda / 2.0)**2
            
            c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
            return R * c
        except Exception:
            return None

    @staticmethod
    def is_point_in_polygon(x, y, poly):
        """
        Ray-Casting algorithm to determine if a point (x, y) is inside a polygon poly.
        poly is a list of tuples/lists of coordinates [(x1, y1), (x2, y2), ..., (xN, yN)].
        """
        if isinstance(poly, dict):
            try:
                if poly.get('type') == 'Polygon' and isinstance(poly.get('coordinates'), list):
                    poly = poly['coordinates'][0]
            except Exception:
                return True
        if not poly or not isinstance(poly, list) or len(poly) < 3:
            return True # If invalid polygon, fail open
        try:
            n = len(poly)
            inside = False
            p1x, p1y = poly[0][0], poly[0][1]
            for i in range(n + 1):
                p2x, p2y = poly[i % n][0], poly[i % n][1]
                if y > min(p1y, p2y):
                    if y <= max(p1y, p2y):
                        if x <= max(p1x, p2x):
                            if p1y != p2y:
                                xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                            if p1x == p2x or x <= xinters:
                                inside = not inside
                p1x, p1y = p2x, p2y
            return inside
        except Exception:
            return True # Fail open on format error

    @staticmethod
    def logistics_quote_for(order, items_total=None):
        """The authoritative transport quote for an order's saved pins."""
        from logistics.services.pricing_service import LogisticsPricingService
        return LogisticsPricingService.calculate_quote(
            laundry=getattr(order, 'laundry', None),
            pickup_lat=getattr(order, 'pickup_lat', None),
            pickup_lng=getattr(order, 'pickup_lng', None),
            delivery_lat=getattr(order, 'delivery_lat', None),
            delivery_lng=getattr(order, 'delivery_lng', None),
            items_total=items_total,
        )

    @staticmethod
    def calculate_delivery_fee(order):
        """What the customer pays for the delivery leg (0 while pricing is off)."""
        return FinanceService.logistics_quote_for(order)['delivery_fee']

    @staticmethod
    def calculate_pickup_fee(order):
        """What the customer pays for the pickup leg (0 while pricing is off)."""
        return FinanceService.logistics_quote_for(order)['pickup_fee']

    @staticmethod
    def _stored_breakdown(order):
        """
        The frozen snapshot for an order, or None if it has none.
        Preserves original rates, distances, promo subsidy, and logistics total.
        Later Admin rate changes do NOT alter already-booked orders.
        """
        priced_at = getattr(order, 'priced_at', None)
        if not isinstance(priced_at, datetime):
            return None

        def money(field, default='0.00'):
            value = getattr(order, field, None)
            if value is None:
                return default
            return str(Decimal(str(value)).quantize(Decimal('0.01')))

        def km(field):
            value = getattr(order, field, None)
            return str(value) if value is not None else None

        from logistics.services.pricing_service import TEMPORARY_LOGISTICS_NOTICE, TransportStatus
        delivery_fees_in_app = bool(getattr(order, 'delivery_fees_in_app', False))
        is_promo = bool(getattr(order, 'is_free_delivery_promo', False))
        scope = getattr(order, 'promo_scope', '') or ''
        free_pickup = is_promo and scope in ('', 'PICKUP_AND_DELIVERY', 'PICKUP_ONLY')
        free_delivery = is_promo and scope in ('', 'PICKUP_AND_DELIVERY', 'DELIVERY_ONLY')
        customer_transport = (Decimal(money('pickup_fee')) + Decimal(money('delivery_fee'))).quantize(Decimal('0.01'))
        savings = Decimal(money('logistics_discount'))

        if delivery_fees_in_app:
            status = TransportStatus.FREE_PROMO if (is_promo and customer_transport == 0) else TransportStatus.PRICED
            notice = ''
        elif free_pickup and free_delivery:
            status, notice = TransportStatus.FREE_PROMO, ''
        else:
            status = TransportStatus.NOT_INCLUDED
            notice = getattr(order, 'logistics_notice', '') or TEMPORARY_LOGISTICS_NOTICE

        label = None
        if is_promo:
            label = {'PICKUP_ONLY': 'FREE PICKUP', 'DELIVERY_ONLY': 'FREE DELIVERY'}.get(scope, 'FREE PICKUP & DELIVERY')

        return {
            "items_total": money('items_total'),
            "item_subtotal": money('items_total'),
            "delivery_fee": money('delivery_fee'),
            "pickup_fee": money('pickup_fee'),
            "total_logistics_fee": str(customer_transport),
            "pickup_distance_km": km('pickup_distance_km'),
            "delivery_distance_km": km('delivery_distance_km'),
            "pickup_rate_per_km": money('pickup_rate_per_km'),
            "delivery_rate_per_km": money('delivery_rate_per_km'),
            "nominal_pickup_fee": money('nominal_pickup_fee'),
            "nominal_delivery_fee": money('nominal_delivery_fee'),
            "nominal_logistics_total": money('logistics_nominal_total'),
            "is_promo_free_delivery": is_promo,
            "free_pickup": free_pickup,
            "free_delivery": free_delivery,
            "promo_scope": scope,
            "promo_name": getattr(order, 'promo_name', '') or '',
            "promo_funding_source": getattr(order, 'promo_funding_source', '') or '',
            "promo_label": label,
            "promo_message": f"You saved GHS {savings} on transport." if savings > 0 else "",
            "logistics_discount": str(savings),
            "logistics_pricing_version": getattr(order, 'logistics_pricing_version', '') or '',
            "pricing_version": getattr(order, 'logistics_pricing_version', '') or '',
            "logistics_notice": notice,
            "transport_status": status,
            "quote_available": True,
            "pricing_enabled": delivery_fees_in_app,
            "discount": money('discount_amount'),
            "tax": money('tax_amount'),
            "platform_fee": money('platform_fee'),
            "total": money('total_amount'),
            "grand_total": money('total_amount'),
            "currency": getattr(order, 'currency', None) or 'GHS',
            "delivery_fees_in_app": delivery_fees_in_app,
        }

    @staticmethod
    def freeze_price_breakdown(order, coupon=None):
        """
        Compute the breakdown once and store it on the order.
        Called when the order is created. Everything afterwards reads the
        stored values, so Admin rate changes or laundry price edits
        cannot alter what this customer was charged.
        """
        breakdown = FinanceService.calculate_price_breakdown(order, coupon=coupon, use_snapshot=False)

        def dec(key):
            return Decimal(str(breakdown.get(key) or '0.00'))

        def km(key):
            value = breakdown.get(key)
            return Decimal(str(value)) if value is not None else None

        order.items_total = dec('items_total')
        order.delivery_fee = dec('delivery_fee')
        order.pickup_fee = dec('pickup_fee')
        order.discount_amount = dec('discount')
        order.tax_amount = dec('tax')
        order.platform_fee = dec('platform_fee')
        order.total_amount = dec('total')
        order.currency = breakdown['currency']
        order.delivery_fees_in_app = breakdown['delivery_fees_in_app']

        # Logistics snapshot: distances, rates, rider cost and promo as they
        # stood at booking. Later admin changes never touch these.
        order.pickup_distance_km = km('pickup_distance_km')
        order.delivery_distance_km = km('delivery_distance_km')
        order.pickup_rate_per_km = dec('pickup_rate_per_km')
        order.delivery_rate_per_km = dec('delivery_rate_per_km')
        order.nominal_pickup_fee = dec('nominal_pickup_fee')
        order.nominal_delivery_fee = dec('nominal_delivery_fee')
        order.logistics_nominal_total = dec('nominal_logistics_total')
        order.logistics_pricing_version = breakdown.get('logistics_pricing_version') or ''
        order.is_free_delivery_promo = bool(breakdown.get('is_promo_free_delivery', False))
        order.promo_funding_source = breakdown.get('promo_funding_source') or ''
        order.promo_scope = breakdown.get('promo_scope') or ''
        order.promo_name = (breakdown.get('promo_name') or '')[:80]
        order.logistics_discount = dec('logistics_discount')
        order.logistics_notice = breakdown.get('logistics_notice') or ''

        order.priced_at = timezone.now()
        order.save(update_fields=[
            'items_total', 'delivery_fee', 'pickup_fee', 'discount_amount',
            'tax_amount', 'platform_fee', 'total_amount', 'currency',
            'delivery_fees_in_app', 'pickup_distance_km', 'delivery_distance_km',
            'pickup_rate_per_km', 'delivery_rate_per_km', 'nominal_pickup_fee',
            'nominal_delivery_fee', 'logistics_nominal_total', 'logistics_pricing_version',
            'is_free_delivery_promo', 'promo_funding_source', 'promo_scope', 'promo_name',
            'logistics_discount', 'logistics_notice', 'priced_at', 'updated_at',
        ])
        return breakdown

    @staticmethod
    def coupon_discount(coupon, items_total, user=None, laundry_id=None):
        """A coupon's discount on the items, never more than the items."""
        if not coupon:
            return Decimal('0.00')
        is_valid, _error = coupon.is_valid(user=user, laundry_id=laundry_id, order_value=items_total)
        if not is_valid:
            return Decimal('0.00')
        if coupon.discount_type == 'FIXED':
            discount = Decimal(str(coupon.discount_value))
        else:
            discount = items_total * (Decimal(str(coupon.discount_value)) / 100)
        return min(discount, items_total)

    @staticmethod
    def compose_breakdown(items_total, quote, discount=Decimal('0.00')):
        """
        The single formula for what a customer pays:

            items - discount + tax + platform fee + pickup + delivery

        Used by the checkout estimate and by the frozen order snapshot, so the
        total the app shows is the total Paystack charges.
        """
        from logistics.services.pricing_service import LogisticsPricingService

        items_total = Decimal(str(items_total))
        taxable_amount = max(Decimal('0.00'), items_total - discount)
        tax = FinanceService.calculate_tax_amount(taxable_amount)
        platform_fee = FinanceService.calculate_platform_fee(taxable_amount)
        pickup_fee = Decimal(str(quote['pickup_fee']))
        delivery_fee = Decimal(str(quote['delivery_fee']))
        total = (taxable_amount + delivery_fee + pickup_fee + tax + platform_fee).quantize(Decimal('0.01'))

        breakdown = LogisticsPricingService.serialize_quote(quote)
        breakdown.update({
            "items_total": str(items_total.quantize(Decimal('0.01'))),
            "item_subtotal": str(items_total.quantize(Decimal('0.01'))),
            "delivery_fee": str(delivery_fee.quantize(Decimal('0.01'))),
            "pickup_fee": str(pickup_fee.quantize(Decimal('0.01'))),
            "logistics_pricing_version": quote['pricing_version'],
            "discount": str(discount.quantize(Decimal('0.01'))),
            "tax": str(tax.quantize(Decimal('0.01'))),
            "platform_fee": str(platform_fee.quantize(Decimal('0.01'))),
            "total": str(total),
            "grand_total": str(total),
            "currency": "GHS",
        })
        return breakdown

    @staticmethod
    def calculate_price_breakdown(order, coupon=None, use_snapshot=True):
        """
        Full financial breakdown for an order.

        Returns the frozen snapshot when the order has one. Pass
        ``use_snapshot=False`` to force a live recomputation, which only
        ``freeze_price_breakdown`` should need.
        """
        if use_snapshot:
            stored = FinanceService._stored_breakdown(order)
            if stored is not None:
                return stored

        items_total = order.items.aggregate(
            total=Sum(F('quantity') * F('price'))
        )['total'] or Decimal('0.00')
        discount = FinanceService.coupon_discount(
            coupon, items_total, user=getattr(order, 'user', None), laundry_id=getattr(order, 'laundry_id', None),
        )
        quote = FinanceService.logistics_quote_for(order, items_total=items_total)
        return FinanceService.compose_breakdown(items_total, quote, discount)
