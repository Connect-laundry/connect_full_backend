import logging
import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple, Dict, Any

from django.conf import settings
from django.utils import timezone

from logistics.models import LogisticsPricingConfig

logger = logging.getLogger(__name__)

TEMPORARY_LOGISTICS_NOTICE = (
    "Pickup and delivery charges are not included in this total yet. "
    "Simame will confirm the logistics cost separately."
)


class LogisticsPricingService:
    """
    Authoritative service for logistics pricing, distance calculations,
    promotional discounts, and server quotes.
    
    Zero mobile app code update is required when admin changes rates or toggles pricing.
    """

    @staticmethod
    def calculate_distance_km(lat1, lon1, lat2, lon2, precision: int = 1) -> Optional[Decimal]:
        """
        Calculates Haversine distance in kilometres between two coordinates,
        rounding to the configured decimal places.
        """
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return None

        R = 6371.0  # Earth radius in km
        try:
            phi1 = math.radians(float(lat1))
            phi2 = math.radians(float(lat2))
            delta_phi = math.radians(float(lat2) - float(lat1))
            delta_lambda = math.radians(float(lon2) - float(lon1))

            a = math.sin(delta_phi / 2.0)**2 + \
                math.cos(phi1) * math.cos(phi2) * \
                math.sin(delta_lambda / 2.0)**2

            c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
            dist_km = R * c
            
            # Format to required precision
            quantum = Decimal('1') if precision <= 0 else Decimal('10') ** -precision
            return Decimal(str(dist_km)).quantize(quantum, rounding=ROUND_HALF_UP)
        except Exception as exc:
            logger.warning("Distance calculation failed", extra={"error": str(exc)})
            return None

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
    ) -> Dict[str, Any]:
        """
        Authoritative calculation of pickup and delivery fees.
        When pricing is disabled, returns GHS 0.00 with the required informational notice.
        """
        config = LogisticsPricingConfig.get_active()

        # If no config or pricing is globally disabled
        if not config or not config.pricing_enabled:
            return {
                "pricing_enabled": False,
                "delivery_fees_in_app": False,
                "pickup_distance_km": None,
                "delivery_distance_km": None,
                "pickup_rate_per_km": Decimal('0.00'),
                "delivery_rate_per_km": Decimal('0.00'),
                "nominal_pickup_fee": Decimal('0.00'),
                "nominal_delivery_fee": Decimal('0.00'),
                "nominal_logistics_total": Decimal('0.00'),
                "pickup_fee": Decimal('0.00'),
                "delivery_fee": Decimal('0.00'),
                "total_logistics_fee": Decimal('0.00'),
                "logistics_discount": Decimal('0.00'),
                "is_promo_free_delivery": False,
                "promo_funding_source": "",
                "promo_label": None,
                "pricing_version": f"v{config.version}" if config else "v1",
                "outside_service_area": False,
                "warning": None,
                "logistics_notice": TEMPORARY_LOGISTICS_NOTICE,
            }

        # Dynamic pricing is active!
        precision = config.distance_rounding_precision or 1
        laundry_lat = getattr(laundry, 'latitude', None)
        laundry_lng = getattr(laundry, 'longitude', None)

        pickup_dist_km = None
        delivery_dist_km = None
        outside_service_area = False
        warning_msg = None

        if laundry_lat is not None and laundry_lng is not None:
            # Pickup distance (customer pickup coords <-> laundry coords)
            if pickup_lat is not None and pickup_lng is not None:
                pickup_dist_km = cls.calculate_distance_km(
                    pickup_lat, pickup_lng, laundry_lat, laundry_lng, precision
                )
            
            # Delivery distance (customer delivery coords <-> laundry coords)
            # Default to pickup coords if delivery coords are not provided
            eff_del_lat = delivery_lat if delivery_lat is not None else pickup_lat
            eff_del_lng = delivery_lng if delivery_lng is not None else pickup_lng
            if eff_del_lat is not None and eff_del_lng is not None:
                delivery_dist_km = cls.calculate_distance_km(
                    eff_del_lat, eff_del_lng, laundry_lat, laundry_lng, precision
                )

        # Service area limits check
        max_dist = max(
            [d for d in [pickup_dist_km, delivery_dist_km] if d is not None] or [Decimal('0.00')]
        )
        if config.max_service_distance_km and max_dist > config.max_service_distance_km:
            outside_service_area = True
            warning_msg = (
                f"Distance ({max_dist:.1f} km) exceeds maximum service limit "
                f"of {config.max_service_distance_km:.1f} km."
            )
        elif laundry and getattr(laundry, 'service_radius_km', None) and max_dist > Decimal(str(laundry.service_radius_km)):
            outside_service_area = True
            warning_msg = (
                f"Distance ({max_dist:.1f} km) is outside {laundry.name}'s "
                f"service radius of {laundry.service_radius_km:.1f} km."
            )

        # Calculate nominal pickup fee
        pickup_nominal = Decimal('0.00')
        if is_pickup and pickup_dist_km is not None:
            calc_p = config.pickup_base_fee + (pickup_dist_km * config.pickup_price_per_km)
            pickup_nominal = max(config.pickup_min_fee, calc_p).quantize(Decimal('0.01'))
        elif is_pickup and config.pickup_base_fee > Decimal('0.00'):
            pickup_nominal = config.pickup_base_fee.quantize(Decimal('0.01'))

        # Calculate nominal delivery fee
        delivery_nominal = Decimal('0.00')
        if is_delivery and delivery_dist_km is not None:
            calc_d = config.delivery_base_fee + (delivery_dist_km * config.delivery_price_per_km)
            delivery_nominal = max(config.delivery_min_fee, calc_d).quantize(Decimal('0.01'))
        elif is_delivery and config.delivery_base_fee > Decimal('0.00'):
            delivery_nominal = config.delivery_base_fee.quantize(Decimal('0.01'))

        nominal_total = (pickup_nominal + delivery_nominal).quantize(Decimal('0.01'))

        # Check Free Pickup & Delivery Promotion
        is_promo = False
        promo_funding = ""
        promo_label = None
        logistics_discount = Decimal('0.00')
        final_pickup_fee = pickup_nominal
        final_delivery_fee = delivery_nominal

        if laundry and hasattr(laundry, 'is_free_delivery_promo_active'):
            if laundry.is_free_delivery_promo_active(distance_km=max_dist):
                is_promo = True
                promo_funding = getattr(laundry, 'promo_funding_source', 'LAUNDRY')
                promo_label = f"Courtesy of {laundry.name}"
                logistics_discount = nominal_total
                final_pickup_fee = Decimal('0.00')
                final_delivery_fee = Decimal('0.00')

        final_total = (final_pickup_fee + final_delivery_fee).quantize(Decimal('0.01'))

        return {
            "pricing_enabled": True,
            "delivery_fees_in_app": True,
            "pickup_distance_km": pickup_dist_km,
            "delivery_distance_km": delivery_dist_km,
            "pickup_rate_per_km": config.pickup_price_per_km,
            "delivery_rate_per_km": config.delivery_price_per_km,
            "nominal_pickup_fee": pickup_nominal,
            "nominal_delivery_fee": delivery_nominal,
            "nominal_logistics_total": nominal_total,
            "pickup_fee": final_pickup_fee,
            "delivery_fee": final_delivery_fee,
            "total_logistics_fee": final_total,
            "logistics_discount": logistics_discount,
            "is_promo_free_delivery": is_promo,
            "promo_funding_source": promo_funding,
            "promo_label": promo_label,
            "pricing_version": f"v{config.version}",
            "outside_service_area": outside_service_area,
            "warning": warning_msg,
            # Once pricing is active, the temporary notice disappears automatically
            "logistics_notice": "",
        }
