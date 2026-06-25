"""Weather-triggered promotional campaigns."""
from datetime import datetime, timedelta, timezone as dt_timezone
import logging

import requests
from django.conf import settings
from django.utils import timezone

from marketplace.models import Notification, NotificationCampaign

logger = logging.getLogger(__name__)


RAINY_WEATHER_CODES = {
    51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99,
}


class WeatherCampaignService:
    @staticmethod
    def _float_setting(name):
        value = getattr(settings, name, '')
        if value in (None, ''):
            return None
        return float(value)

    @classmethod
    def _fetch_open_meteo(cls):
        latitude = cls._float_setting('WEATHER_PROMO_LATITUDE')
        longitude = cls._float_setting('WEATHER_PROMO_LONGITUDE')
        if latitude is None or longitude is None:
            logger.info("Rainy promo skipped: missing weather coordinates")
            return None

        response = requests.get(
            settings.WEATHER_PROMO_OPEN_METEO_URL,
            params={
                'latitude': latitude,
                'longitude': longitude,
                'hourly': 'precipitation_probability,rain,showers,weather_code',
                'forecast_days': 1,
                'timezone': 'UTC',
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    @classmethod
    def _rain_signal(cls, payload):
        hourly = (payload or {}).get('hourly') or {}
        times = hourly.get('time') or []
        probabilities = hourly.get('precipitation_probability') or []
        rain = hourly.get('rain') or []
        showers = hourly.get('showers') or []
        codes = hourly.get('weather_code') or []

        now = timezone.now().astimezone(dt_timezone.utc)
        horizon = now + timedelta(hours=settings.WEATHER_PROMO_LOOKAHEAD_HOURS)
        threshold = settings.WEATHER_PROMO_RAIN_PROBABILITY_THRESHOLD
        min_rain_mm = settings.WEATHER_PROMO_MIN_RAIN_MM

        for index, raw_time in enumerate(times):
            try:
                forecast_time = datetime.fromisoformat(raw_time)
            except (TypeError, ValueError):
                continue
            if forecast_time.tzinfo is None:
                forecast_time = forecast_time.replace(tzinfo=dt_timezone.utc)
            forecast_time = forecast_time.astimezone(dt_timezone.utc)
            if forecast_time < now or forecast_time > horizon:
                continue

            probability = int(probabilities[index] or 0) if index < len(probabilities) else 0
            rain_mm = float(rain[index] or 0) if index < len(rain) else 0.0
            rain_mm += float(showers[index] or 0) if index < len(showers) else 0.0
            weather_code = int(codes[index]) if index < len(codes) and codes[index] is not None else None

            if (
                probability >= threshold
                or rain_mm >= min_rain_mm
                or weather_code in RAINY_WEATHER_CODES
            ):
                return {
                    'probability': probability,
                    'rain_mm': rain_mm,
                    'weather_code': weather_code,
                    'forecast_time': forecast_time.isoformat(),
                }
        return None

    @classmethod
    def enqueue_rainy_day_campaign(cls):
        if not getattr(settings, 'WEATHER_PROMO_ENABLED', False):
            return None
        if settings.WEATHER_PROMO_PROVIDER != 'open-meteo':
            logger.warning(
                "Rainy promo skipped: unsupported weather provider",
                extra={'provider': settings.WEATHER_PROMO_PROVIDER},
            )
            return None

        signal = cls._rain_signal(cls._fetch_open_meteo())
        if not signal:
            logger.info("Rainy promo skipped: no rain signal in forecast window")
            return None

        today_key = timezone.localdate().isoformat()
        name = f'Rainy day promo {today_key}'
        existing = NotificationCampaign.objects.filter(name=name).first()
        if existing:
            return existing

        campaign = NotificationCampaign.objects.create(
            name=name,
            segment=NotificationCampaign.Segment.PROMO_OPT_IN,
            segment_params={'weather': signal},
            title=settings.WEATHER_PROMO_TITLE,
            body=settings.WEATHER_PROMO_BODY,
            action_url=settings.WEATHER_PROMO_ACTION_URL,
            notification_type=Notification.Type.PROMO,
            category='PROMO',
            status=NotificationCampaign.Status.SCHEDULED,
            scheduled_for=timezone.now(),
        )

        from marketplace.tasks import run_campaign
        run_campaign.delay(str(campaign.id))
        logger.info(
            "Rainy day promo campaign queued",
            extra={'campaign_id': str(campaign.id), 'forecast': signal},
        )
        return campaign
