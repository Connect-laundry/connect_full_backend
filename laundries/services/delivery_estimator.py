import math
from datetime import datetime
from django.utils import timezone


class DeliveryEstimator:
    """
    Service to calculate dynamic delivery time estimates.
    """

    @staticmethod
    def calculate_haversine_distance(lat1, lon1, lat2, lon2):
        """
        Calculate the great circle distance between two points
        on the earth (specified in decimal degrees)
        """
        # Convert decimal degrees to radians
        try:
            points = [float(lat1), float(lon1), float(lat2), float(lon2)]
            lat1, lon1, lat2, lon2 = map(math.radians, points)
        except (ValueError, TypeError):
            # Fallback distance if coordinates are invalid
            return 0.0

        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.asin(math.sqrt(a))
        r = 6371  # Radius of earth in kilometers
        return c * r

    def get_estimated_delivery_time(
        self, laundry, user_lat=None, user_lng=None, active_order_count=0
    ):
        """
        Main calculation logic for delivery estimation.
        - Base time = laundry.estimated_delivery_hours
        - +10 mins per active order
        - +5 mins per 5 km distance
        - Peak hour (5pm-9pm) = +15% multiplier
        - Cap at 48 hours
        """
        import logging

        logger = logging.getLogger(__name__)

        try:
            base_hours = laundry.estimated_delivery_hours or 24
            total_minutes = base_hours * 60

            # 1. Queue Delay (+10 mins per active order)
            total_minutes += int(active_order_count or 0) * 10

            # 2. Distance Delay (+5 mins per 5 km)
            if user_lat is not None and user_lng is not None:
                try:
                    distance = self.calculate_haversine_distance(
                        laundry.latitude, laundry.longitude, user_lat, user_lng
                    )
                    distance_delay = (distance / 5) * 5
                    total_minutes += distance_delay
                except Exception as e:
                    logger.warning(f"Distance calculation failed for laundry {
                            laundry.id}: {e}")

            # 3. Peak Hour Surge (+15%)
            # Peak hours: 17:00 to 21:00
            try:
                now = timezone.localtime()
                if 17 <= now.hour < 21:
                    total_minutes *= 1.15
            except Exception as e:
                logger.warning(f"Peak hour check failed: {e}")

            # 4. Caps
            # Minimum is base time
            total_minutes = max(total_minutes, base_hours * 60)
            # Maximum cap at 48 hours
            total_minutes = min(total_minutes, 48 * 60)

            return self.format_duration(total_minutes)
        except Exception as e:
            logger.error(f"Error calculating delivery time for laundry {
                    laundry.id}: {e}", exc_info=True)
            return f"{laundry.estimated_delivery_hours or 24}h 0m"

    def format_duration(self, total_minutes):
        """Format minutes into '3h 25m' string."""
        hours = int(total_minutes // 60)
        minutes = int(total_minutes % 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
