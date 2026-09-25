"""
The one place pickup and delivery are priced.

Everything that shows or charges transport (the checkout estimate, order
creation, the frozen order snapshot, Paystack, settlements) reads the quote
built here, and every number in it comes from the admin-editable
``LogisticsPricingConfig``. The mobile app renders the quote and never computes
money itself, so changing a rate in admin changes the next quote in every
installed app without a release.

Distance is the straight line between the two map pins, multiplied by the
admin's ``road_distance_factor`` to approximate the road route. There is no
routing API in the stack; a straight-line model cannot fail at request time, so
the only "quote unavailable" cases are missing or invalid coordinates.
"""

import logging
import math
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, Optional

from logistics.models import LogisticsPricingConfig

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')
CENT = Decimal('0.01')

TEMPORARY_LOGISTICS_NOTICE = (
    "Pickup and delivery fees are not included in the amount shown. "
    "Simame will confirm the transport charge separately."
)


class TransportStatus:
    """What the customer is told about transport. The app switches on this."""
    #: Pricing is off in admin: transport is not in the total.
    NOT_INCLUDED = 'NOT_INCLUDED'
    #: Pricing is on and the fees are in the total.
    PRICED = 'PRICED'
    #: A promo makes all transport free to the customer.
    FREE_PROMO = 'FREE_PROMO'
    #: Pricing is on but this trip cannot be priced (no/invalid pins, too far).
    UNAVAILABLE = 'UNAVAILABLE'


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _to_decimal(value) -> Optional[Decimal]:
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def valid_coordinates(lat, lng) -> bool:
    """A usable map pin: numeric, in range, and not the (0, 0) GPS default."""
    lat_d, lng_d = _to_decimal(lat), _to_decimal(lng)
    if lat_d is None or lng_d is None:
        return False
    if not (Decimal('-90') <= lat_d <= Decimal('90')) or not (Decimal('-180') <= lng_d <= Decimal('180')):
        return False
    return not (lat_d == 0 and lng_d == 0)


class LogisticsPricingService:
    """
    Authoritative service for logistics pricing, distance calculations,
    promotional discounts, and server quotes.
    """

    @staticmethod
    def calculate_distance_km(lat1, lon1, lat2, lon2, precision: int = 1) -> Optional[Decimal]:
        """Great-circle (Haversine) distance in km, rounded to `precision` places."""
        if not (valid_coordinates(lat1, lon1) and valid_coordinates(lat2, lon2)):
            return None
        R = 6371.0  # Earth radius in km
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        delta_phi = math.radians(float(lat2) - float(lat1))
        delta_lambda = math.radians(float(lon2) - float(lon1))
        a = (
            math.sin(delta_phi / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
        )
        dist_km = R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        quantum = Decimal('1') if precision <= 0 else Decimal('10') ** -precision
        return Decimal(str(dist_km)).quantize(quantum, rounding=ROUND_HALF_UP)

    @classmethod
    def billable_distance_km(cls, config, lat1, lon1, lat2, lon2) -> Optional[Decimal]:
        """Straight line x road factor, rounded, raised to the billable minimum."""
        precision = config.distance_rounding_precision if config.distance_rounding_precision is not None else 1
        straight = cls.calculate_distance_km(lat1, lon1, lat2, lon2, precision=6)
        if straight is None:
            return None
        factor = Decimal(str(config.road_distance_factor or 1))
        quantum = Decimal('1') if precision <= 0 else Decimal('10') ** -precision
        distance = (straight * factor).quantize(quantum, rounding=ROUND_HALF_UP)
        minimum = Decimal(str(config.minimum_billable_distance_km or 0))
        return max(distance, minimum)

    @staticmethod
    def leg_fee(distance_km: Decimal, rate, base, minimum) -> Decimal:
        fee = Decimal(str(base or 0)) + distance_km * Decimal(str(rate or 0))
        return _money(max(Decimal(str(minimum or 0)), fee))

    @staticmethod
    def _promo_fields(laundry, applies: bool, pickup_free: bool, delivery_free: bool, savings: Decimal) -> Dict[str, Any]:
        if not applies:
            return {
                "is_promo_free_delivery": False,
                "free_pickup": False,
                "free_delivery": False,
                "promo_scope": "",
                "promo_name": "",
                "promo_label": None,
                "promo_funding_source": "",
                "promo_message": "",
            }
        message = f"You saved GHS {savings} on transport." if savings > 0 else ""
        return {
            "is_promo_free_delivery": True,
            "free_pickup": pickup_free,
            "free_delivery": delivery_free,
            "promo_scope": laundry.promo_scope,
            "promo_name": laundry.promo_name or "",
            "promo_label": laundry.promo_display_label(),
            "promo_funding_source": laundry.promo_funding_source,
            "promo_message": message,
        }

    @classmethod
    def calculate_quote(
        cls,
        laundry,
        pickup_lat=None,
        pickup_lng=None,
        delivery_lat=None,
        delivery_lng=None,
        is_pickup: bool = True,
        is_delivery: bool = True,
        items_total=None,
    ) -> Dict[str, Any]:
        """
        Price both legs for one trip.

        PICKUP   customer pickup pin  -> laundry
        DELIVERY laundry              -> customer delivery pin
        (the delivery pin falls back to the pickup pin when absent)

        Never invents a price: when pricing is on and a leg cannot be measured,
        the quote comes back ``quote_available=False`` and order creation refuses
        it.
        """
        config = LogisticsPricingConfig.get_active()
        pricing_enabled = bool(config and config.pricing_enabled)
        version = f"v{config.version}" if config else ""

        if delivery_lat is None or delivery_lng is None:
            delivery_lat, delivery_lng = pickup_lat, pickup_lng

        laundry_lat = getattr(laundry, 'latitude', None) if laundry is not None else None
        laundry_lng = getattr(laundry, 'longitude', None) if laundry is not None else None
        items_total_d = _to_decimal(items_total)

        base = {
            "pricing_enabled": pricing_enabled,
            "delivery_fees_in_app": pricing_enabled,
            "currency": "GHS",
            "pricing_version": version,
            "pickup_distance_km": None,
            "delivery_distance_km": None,
            "pickup_rate_per_km": ZERO,
            "delivery_rate_per_km": ZERO,
            "pickup_base_fee": ZERO,
            "delivery_base_fee": ZERO,
            "nominal_pickup_fee": ZERO,
            "nominal_delivery_fee": ZERO,
            "nominal_logistics_total": ZERO,
            "pickup_fee": ZERO,
            "delivery_fee": ZERO,
            "total_logistics_fee": ZERO,
            "logistics_discount": ZERO,
            "outside_service_area": False,
            "warning": None,
            "quote_available": True,
            "unavailable_reason": "",
        }

        # ---- Pricing OFF: transport is not in the total -------------------
        if not pricing_enabled:
            measured = [d for d in (
                cls.calculate_distance_km(pickup_lat, pickup_lng, laundry_lat, laundry_lng),
                cls.calculate_distance_km(delivery_lat, delivery_lng, laundry_lat, laundry_lng),
            ) if d is not None]
            straight = max(measured) if measured else None
            promo = (
                laundry is not None
                and hasattr(laundry, 'is_free_delivery_promo_active')
                and laundry.is_free_delivery_promo_active(distance_km=straight, items_total=items_total_d)
            )
            pickup_free = bool(promo and laundry.promo_covers_pickup)
            delivery_free = bool(promo and laundry.promo_covers_delivery)
            if pickup_free and delivery_free:
                status, notice = TransportStatus.FREE_PROMO, ""
            elif pickup_free:
                status = TransportStatus.NOT_INCLUDED
                notice = ("Pickup is free. The delivery fee is not included in the amount shown; "
                          "Simame will confirm it separately.")
            elif delivery_free:
                status = TransportStatus.NOT_INCLUDED
                notice = ("Delivery is free. The pickup fee is not included in the amount shown; "
                          "Simame will confirm it separately.")
            else:
                status, notice = TransportStatus.NOT_INCLUDED, TEMPORARY_LOGISTICS_NOTICE
            return {
                **base,
                **cls._promo_fields(laundry, promo, pickup_free, delivery_free, ZERO),
                "transport_status": status,
                "logistics_notice": notice,
            }

        # ---- Pricing ON -----------------------------------------------------
        base.update({
            "pickup_rate_per_km": config.pickup_price_per_km,
            "delivery_rate_per_km": config.delivery_price_per_km,
            "pickup_base_fee": config.pickup_base_fee,
            "delivery_base_fee": config.delivery_base_fee,
        })

        def unavailable(reason, outside=False):
            return {
                **base,
                **cls._promo_fields(laundry, False, False, False, ZERO),
                "transport_status": TransportStatus.UNAVAILABLE,
                "quote_available": False,
                "unavailable_reason": reason,
                "outside_service_area": outside,
                "warning": reason,
                "logistics_notice": reason,
            }

        if not valid_coordinates(laundry_lat, laundry_lng):
            return unavailable("This laundry's location is not set, so transport cannot be priced yet.")
        if is_pickup and not valid_coordinates(pickup_lat, pickup_lng):
            return unavailable("Choose your pickup location on the map to see the pickup fee.")
        if is_delivery and not valid_coordinates(delivery_lat, delivery_lng):
            return unavailable("Choose your delivery location on the map to see the delivery fee.")

        pickup_km = cls.billable_distance_km(config, pickup_lat, pickup_lng, laundry_lat, laundry_lng) if is_pickup else None
        delivery_km = cls.billable_distance_km(config, delivery_lat, delivery_lng, laundry_lat, laundry_lng) if is_delivery else None
        base["pickup_distance_km"] = pickup_km
        base["delivery_distance_km"] = delivery_km

        longest = max([d for d in (pickup_km, delivery_km) if d is not None] or [ZERO])
        max_km = Decimal(str(config.max_service_distance_km))
        if longest > max_km:
            return unavailable(
                f"This address is {longest:.1f} km from the laundry. Simame delivers up to {max_km:.1f} km.",
                outside=True,
            )
        radius = getattr(laundry, 'service_radius_km', None)
        if radius:
            straight_longest = max([d for d in (
                cls.calculate_distance_km(pickup_lat, pickup_lng, laundry_lat, laundry_lng) if is_pickup else None,
                cls.calculate_distance_km(delivery_lat, delivery_lng, laundry_lat, laundry_lng) if is_delivery else None,
            ) if d is not None] or [ZERO])
            if straight_longest > Decimal(str(radius)):
                return unavailable(
                    f"This address is {straight_longest:.1f} km away, outside {laundry.name}'s "
                    f"{Decimal(str(radius)):.1f} km service area.",
                    outside=True,
                )

        nominal_pickup = cls.leg_fee(pickup_km, config.pickup_price_per_km, config.pickup_base_fee, config.pickup_min_fee) if pickup_km is not None else ZERO
        nominal_delivery = cls.leg_fee(delivery_km, config.delivery_price_per_km, config.delivery_base_fee, config.delivery_min_fee) if delivery_km is not None else ZERO
        nominal_total = _money(nominal_pickup + nominal_delivery)

        promo = (
            hasattr(laundry, 'is_free_delivery_promo_active')
            and laundry.is_free_delivery_promo_active(distance_km=longest, items_total=items_total_d)
        )
        pickup_free = bool(promo and laundry.promo_covers_pickup)
        delivery_free = bool(promo and laundry.promo_covers_delivery)
        savings = _money((nominal_pickup if pickup_free else ZERO) + (nominal_delivery if delivery_free else ZERO))

        # A laundry-funded promo is paid out of the laundry's share of this
        # order. If the items cannot cover the rider, the promo is not applied
        # rather than leaving the rider short.
        if promo and savings > 0 and laundry.promo_funding_source == 'LAUNDRY' and items_total_d is not None:
            if savings > items_total_d:
                promo, pickup_free, delivery_free, savings = False, False, False, ZERO

        pickup_fee = ZERO if pickup_free else nominal_pickup
        delivery_fee = ZERO if delivery_free else nominal_delivery
        customer_total = _money(pickup_fee + delivery_fee)

        status = TransportStatus.FREE_PROMO if (promo and customer_total == 0) else TransportStatus.PRICED
        return {
            **base,
            **cls._promo_fields(laundry, promo, pickup_free, delivery_free, savings),
            "nominal_pickup_fee": nominal_pickup,
            "nominal_delivery_fee": nominal_delivery,
            "nominal_logistics_total": nominal_total,
            "pickup_fee": pickup_fee,
            "delivery_fee": delivery_fee,
            "total_logistics_fee": customer_total,
            "logistics_discount": savings,
            "transport_status": status,
            # Once pricing is on, the "not included" notice disappears by itself.
            "logistics_notice": "",
        }

    @staticmethod
    def serialize_quote(quote: Dict[str, Any]) -> Dict[str, Any]:
        """JSON-safe copy: money and distances as strings, like the rest of the API."""
        out = {}
        for key, value in quote.items():
            if isinstance(value, Decimal):
                out[key] = str(value)
            else:
                out[key] = value
        return out


def laundry_logistics_summary(laundry) -> Dict[str, Any]:
    """
    What the laundry list and detail screens show about transport before any
    address is known: whether it is priced, the notice, and a running promo.
    """
    config = LogisticsPricingConfig.get_active()
    pricing_enabled = bool(config and config.pricing_enabled)
    running = bool(laundry is not None and hasattr(laundry, 'is_promo_running') and laundry.is_promo_running())
    full_promo = running and laundry.promo_covers_pickup and laundry.promo_covers_delivery
    if pricing_enabled:
        notice = ""
        status = TransportStatus.PRICED
    elif full_promo:
        notice = ""
        status = TransportStatus.FREE_PROMO
    else:
        notice = TEMPORARY_LOGISTICS_NOTICE
        status = TransportStatus.NOT_INCLUDED
    promo = None
    if running:
        promo = {
            "active": True,
            "scope": laundry.promo_scope,
            "label": laundry.promo_display_label(),
            "name": laundry.promo_name or "",
            "ends_at": laundry.promo_end_at.isoformat() if laundry.promo_end_at else None,
            "min_order_value": str(laundry.promo_min_order_value) if laundry.promo_min_order_value is not None else None,
            "max_distance_km": str(laundry.promo_max_distance_km) if laundry.promo_max_distance_km is not None else None,
        }
    return {
        "pricing_enabled": pricing_enabled,
        "transport_status": status,
        "notice": notice,
        "pickup_rate_per_km": str(config.pickup_price_per_km) if pricing_enabled else None,
        "delivery_rate_per_km": str(config.delivery_price_per_km) if pricing_enabled else None,
        "pricing_version": f"v{config.version}" if config else "",
        "promo": promo,
    }
