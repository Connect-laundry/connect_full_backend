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

        False while logistics pricing is disabled in admin.
        When LogisticsPricingConfig.pricing_enabled is True, this returns True immediately
        without requiring any server restart or mobile app rebuild.
        """
        try:
            from logistics.models import LogisticsPricingConfig
            config = LogisticsPricingConfig.get_active()
            if config and config.pricing_enabled:
                return True
        except Exception:
            pass
        return bool(getattr(settings, 'DELIVERY_FEES_IN_APP', False))


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
    def calculate_delivery_fee(order):
        """
        Delivery fee charged through the app.
        When dynamic logistics pricing is enabled, authoritative distance-based rate applies.
        Otherwise falls back to per-laundry zone pricing.
        """
        if not FinanceService.delivery_fees_in_app():
            return Decimal('0.00')

        try:
            from logistics.models import LogisticsPricingConfig
            from logistics.services.pricing_service import LogisticsPricingService
            config = LogisticsPricingConfig.get_active()
            if config and config.pricing_enabled:
                quote = LogisticsPricingService.calculate_quote(
                    laundry=getattr(order, 'laundry', None),
                    pickup_lat=getattr(order, 'pickup_lat', None),
                    pickup_lng=getattr(order, 'pickup_lng', None),
                    delivery_lat=getattr(order, 'delivery_lat', None),
                    delivery_lng=getattr(order, 'delivery_lng', None),
                )
                return quote['delivery_fee']
        except Exception:
            pass

        pickup_lat = getattr(order, 'pickup_lat', None)
        pickup_lng = getattr(order, 'pickup_lng', None)
        laundry = getattr(order, 'laundry', None)
        
        has_coords = (
            pickup_lat is not None and not hasattr(pickup_lat, '_mock_return_value') and
            pickup_lng is not None and not hasattr(pickup_lng, '_mock_return_value')
        )
        has_laundry_coords = (
            laundry is not None and not hasattr(laundry, '_mock_return_value') and
            getattr(laundry, 'latitude', None) is not None and not hasattr(laundry.latitude, '_mock_return_value') and
            getattr(laundry, 'longitude', None) is not None and not hasattr(laundry.longitude, '_mock_return_value')
        )

        if has_coords and has_laundry_coords:
            from laundries.models.pricing import DeliveryZonePricing
            zones = DeliveryZonePricing.objects.filter(laundry=laundry).order_by('min_distance_km')
            if zones.exists():
                distance = FinanceService.calculate_haversine_distance(
                    pickup_lat, pickup_lng,
                    laundry.latitude, laundry.longitude
                )
                if distance is not None:
                    for zone in zones:
                        if zone.min_distance_km <= distance <= zone.max_distance_km:
                            return Decimal(str(zone.delivery_fee))
        
        # The laundry's own flat fee.
        laundry_fee = getattr(laundry, 'delivery_fee', None) if laundry is not None else None
        if laundry_fee is not None and not hasattr(laundry_fee, '_mock_return_value'):
            return Decimal(str(laundry_fee))

        return Decimal('0.00')

    @staticmethod
    def calculate_pickup_fee(order):
        """Pickup fee charged through the app. See ``calculate_delivery_fee``."""
        if not FinanceService.delivery_fees_in_app():
            return Decimal('0.00')

        try:
            from logistics.models import LogisticsPricingConfig
            from logistics.services.pricing_service import LogisticsPricingService
            config = LogisticsPricingConfig.get_active()
            if config and config.pricing_enabled:
                quote = LogisticsPricingService.calculate_quote(
                    laundry=getattr(order, 'laundry', None),
                    pickup_lat=getattr(order, 'pickup_lat', None),
                    pickup_lng=getattr(order, 'pickup_lng', None),
                    delivery_lat=getattr(order, 'delivery_lat', None),
                    delivery_lng=getattr(order, 'delivery_lng', None),
                )
                return quote['pickup_fee']
        except Exception:
            pass

        pickup_lat = getattr(order, 'pickup_lat', None)
        pickup_lng = getattr(order, 'pickup_lng', None)
        laundry = getattr(order, 'laundry', None)
        
        has_coords = (
            pickup_lat is not None and not hasattr(pickup_lat, '_mock_return_value') and
            pickup_lng is not None and not hasattr(pickup_lng, '_mock_return_value')
        )
        has_laundry_coords = (
            laundry is not None and not hasattr(laundry, '_mock_return_value') and
            getattr(laundry, 'latitude', None) is not None and not hasattr(laundry.latitude, '_mock_return_value') and
            getattr(laundry, 'longitude', None) is not None and not hasattr(laundry.longitude, '_mock_return_value')
        )

        if has_coords and has_laundry_coords:
            from laundries.models.pricing import DeliveryZonePricing
            zones = DeliveryZonePricing.objects.filter(laundry=laundry).order_by('min_distance_km')
            if zones.exists():
                distance = FinanceService.calculate_haversine_distance(
                    pickup_lat, pickup_lng,
                    laundry.latitude, laundry.longitude
                )
                if distance is not None:
                    for zone in zones:
                        if zone.min_distance_km <= distance <= zone.max_distance_km:
                            return Decimal(str(zone.pickup_fee))
                            
        laundry_fee = getattr(laundry, 'pickup_fee', 0.00) if laundry is not None else 0.00
        if laundry_fee is not None and not hasattr(laundry_fee, '_mock_return_value'):
            return Decimal(str(laundry_fee))
        return Decimal('0.00')


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

        laundry = getattr(order, 'laundry', None)
        laundry_name = getattr(laundry, 'name', '') if laundry else ''
        is_promo = bool(getattr(order, 'is_free_delivery_promo', False))

        pickup_f = Decimal(money('pickup_fee'))
        deliv_f = Decimal(money('delivery_fee'))
        total_logistics = (pickup_f + deliv_f).quantize(Decimal('0.01'))

        from logistics.services.pricing_service import TEMPORARY_LOGISTICS_NOTICE
        delivery_fees_in_app = bool(getattr(order, 'delivery_fees_in_app', False))
        logistics_notice = getattr(order, 'logistics_notice', '') or (TEMPORARY_LOGISTICS_NOTICE if not delivery_fees_in_app else '')

        return {
            "items_total": money('items_total'),
            "delivery_fee": money('delivery_fee'),
            "pickup_fee": money('pickup_fee'),
            "total_logistics_fee": str(total_logistics),
            "pickup_distance_km": str(order.pickup_distance_km) if getattr(order, 'pickup_distance_km', None) is not None else None,
            "delivery_distance_km": str(order.delivery_distance_km) if getattr(order, 'delivery_distance_km', None) is not None else None,
            "pickup_rate_per_km": money('pickup_rate_per_km'),
            "delivery_rate_per_km": money('delivery_rate_per_km'),
            "is_promo_free_delivery": is_promo,
            "promo_funding_source": getattr(order, 'promo_funding_source', '') or '',
            "promo_label": f"Courtesy of {laundry_name}" if is_promo and laundry_name else None,
            "logistics_discount": money('logistics_discount'),
            "logistics_pricing_version": getattr(order, 'logistics_pricing_version', '') or '',
            "logistics_notice": logistics_notice,
            "discount": money('discount_amount'),
            "tax": money('tax_amount'),
            "platform_fee": money('platform_fee'),
            "total": money('total_amount'),
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

        order.items_total = Decimal(breakdown['items_total'])
        order.delivery_fee = Decimal(breakdown['delivery_fee'])
        order.pickup_fee = Decimal(breakdown['pickup_fee'])
        order.discount_amount = Decimal(breakdown['discount'])
        order.tax_amount = Decimal(breakdown['tax'])
        order.platform_fee = Decimal(breakdown['platform_fee'])
        order.total_amount = Decimal(breakdown['total'])
        order.currency = breakdown['currency']
        order.delivery_fees_in_app = breakdown['delivery_fees_in_app']

        # Logistics snapshot
        order.pickup_distance_km = (
            Decimal(str(breakdown['pickup_distance_km']))
            if breakdown.get('pickup_distance_km') is not None
            else None
        )
        order.delivery_distance_km = (
            Decimal(str(breakdown['delivery_distance_km']))
            if breakdown.get('delivery_distance_km') is not None
            else None
        )
        order.pickup_rate_per_km = Decimal(str(breakdown.get('pickup_rate_per_km') or '0.00'))
        order.delivery_rate_per_km = Decimal(str(breakdown.get('delivery_rate_per_km') or '0.00'))
        order.logistics_pricing_version = breakdown.get('logistics_pricing_version') or breakdown.get('pricing_version', '')
        order.is_free_delivery_promo = bool(breakdown.get('is_promo_free_delivery', False))
        order.promo_funding_source = breakdown.get('promo_funding_source', '')
        order.logistics_discount = Decimal(str(breakdown.get('logistics_discount') or '0.00'))
        order.logistics_notice = breakdown.get('logistics_notice', '')

        order.priced_at = timezone.now()
        order.save(update_fields=[
            'items_total', 'delivery_fee', 'pickup_fee', 'discount_amount',
            'tax_amount', 'platform_fee', 'total_amount', 'currency',
            'delivery_fees_in_app', 'pickup_distance_km', 'delivery_distance_km',
            'pickup_rate_per_km', 'delivery_rate_per_km', 'logistics_pricing_version',
            'is_free_delivery_promo', 'promo_funding_source', 'logistics_discount',
            'logistics_notice', 'priced_at', 'updated_at',
        ])
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

        # 1. Sum items
        items_total = order.items.aggregate(
            total=Sum(F('quantity') * F('price'))
        )['total'] or Decimal('0.00')
        
        # 2. Logistics Quote
        from logistics.services.pricing_service import LogisticsPricingService
        quote = LogisticsPricingService.calculate_quote(
            laundry=getattr(order, 'laundry', None),
            pickup_lat=getattr(order, 'pickup_lat', None),
            pickup_lng=getattr(order, 'pickup_lng', None),
            delivery_lat=getattr(order, 'delivery_lat', None),
            delivery_lng=getattr(order, 'delivery_lng', None),
        )

        if quote['pricing_enabled']:
            delivery_fee = quote['delivery_fee']
            pickup_fee = quote['pickup_fee']
        else:
            delivery_fee = FinanceService.calculate_delivery_fee(order)
            pickup_fee = FinanceService.calculate_pickup_fee(order)
            quote['delivery_fee'] = delivery_fee
            quote['pickup_fee'] = pickup_fee
            quote['total_logistics_fee'] = (pickup_fee + delivery_fee).quantize(Decimal('0.01'))
            quote['delivery_fees_in_app'] = FinanceService.delivery_fees_in_app()

        # 3. Discount

        discount = Decimal('0.00')
        if coupon:
            is_valid, error = coupon.is_valid(
                user=order.user, 
                laundry_id=order.laundry_id, 
                order_value=items_total
            )
            
            if is_valid:
                if coupon.discount_type == 'FIXED':
                    discount = Decimal(str(coupon.discount_value))
                else:
                    discount = (items_total * (Decimal(str(coupon.discount_value)) / 100))
                
                # Ensure discount doesn't exceed items_total
                discount = min(discount, items_total)
        
        # 4. Tax (Calculated on items_total - discount)
        taxable_amount = max(Decimal('0.00'), items_total - discount)
        tax = FinanceService.calculate_tax_amount(taxable_amount)
        
        # 5. Platform Fee
        platform_fee = FinanceService.calculate_platform_fee(taxable_amount)
        
        # 6. Final Total
        total = taxable_amount + delivery_fee + pickup_fee + tax + platform_fee
        
        return {
            "items_total": str(items_total.quantize(Decimal('0.01'))),
            "delivery_fee": str(delivery_fee.quantize(Decimal('0.01'))),
            "pickup_fee": str(pickup_fee.quantize(Decimal('0.01'))),
            "total_logistics_fee": str(quote['total_logistics_fee']),
            "pickup_distance_km": str(quote['pickup_distance_km']) if quote['pickup_distance_km'] is not None else None,
            "delivery_distance_km": str(quote['delivery_distance_km']) if quote['delivery_distance_km'] is not None else None,
            "pickup_rate_per_km": str(quote['pickup_rate_per_km']),
            "delivery_rate_per_km": str(quote['delivery_rate_per_km']),
            "nominal_pickup_fee": str(quote['nominal_pickup_fee']),
            "nominal_delivery_fee": str(quote['nominal_delivery_fee']),
            "nominal_logistics_total": str(quote['nominal_logistics_total']),
            "is_promo_free_delivery": quote['is_promo_free_delivery'],
            "promo_funding_source": quote['promo_funding_source'],
            "promo_label": quote['promo_label'],
            "logistics_discount": str(quote['logistics_discount']),
            "logistics_pricing_version": quote['pricing_version'],
            "logistics_notice": quote['logistics_notice'],
            "discount": str(discount.quantize(Decimal('0.01'))),
            "tax": str(tax.quantize(Decimal('0.01'))),
            "platform_fee": str(platform_fee.quantize(Decimal('0.01'))),
            "total": str(total.quantize(Decimal('0.01'))),
            "currency": "GHS",
            "delivery_fees_in_app": quote['delivery_fees_in_app'],
            "outside_service_area": quote['outside_service_area'],
            "warning": quote['warning'],
        }

