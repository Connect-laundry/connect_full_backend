"""Address <-> coordinate geocoding with pluggable commercial providers.

The owner web app captures a business location via GPS, a draggable map pin, an
address search, or manual address entry. Coordinates are *backend-managed*: the
API exposes ``latitude``/``longitude`` as read-only derived values and the owner
sets location either by supplying coordinates (from the map/Places SDK) or by
supplying an address that the backend geocodes here.

Providers:
* ``GoogleGeocoder``  — Google Maps Geocoding API (``GOOGLE_MAPS_API_KEY``).
* ``MapboxGeocoder``  — Mapbox Geocoding API (``MAPBOX_ACCESS_TOKEN``).
* ``NullGeocoder``    — no provider configured; raises ``GeocodingUnavailable``.

Selection is driven by ``settings.GEOCODING_PROVIDER``. When unconfigured, the
geocode endpoint returns HTTP 503 and address-only laundry creation raises a
clear validation error telling the client to supply coordinates instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

# pyre-ignore[missing-module]
import requests
# pyre-ignore[missing-module]
from django.conf import settings

logger = logging.getLogger(__name__)

GEOCODE_TIMEOUT_SECONDS = 6


class GeocodingError(Exception):
    """Generic geocoding failure (network/provider error or no result)."""


class GeocodingUnavailable(GeocodingError):
    """No geocoding provider is configured."""


@dataclass
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str = ''
    place_id: str = ''

    def as_dict(self) -> dict:
        return {
            'latitude': self.latitude,
            'longitude': self.longitude,
            'formatted_address': self.formatted_address,
            'place_id': self.place_id,
        }


class BaseGeocoder:
    name = 'base'

    def geocode(self, address: str) -> GeocodeResult:
        raise NotImplementedError

    def reverse(self, latitude: float, longitude: float) -> GeocodeResult:
        raise NotImplementedError


class NullGeocoder(BaseGeocoder):
    name = 'null'

    def geocode(self, address: str) -> GeocodeResult:
        raise GeocodingUnavailable('Geocoding is not configured on this server.')

    def reverse(self, latitude: float, longitude: float) -> GeocodeResult:
        raise GeocodingUnavailable('Geocoding is not configured on this server.')


class GoogleGeocoder(BaseGeocoder):
    name = 'google'
    ENDPOINT = 'https://maps.googleapis.com/maps/api/geocode/json'

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _request(self, params: dict) -> dict:
        params = {**params, 'key': self.api_key}
        try:
            resp = requests.get(self.ENDPOINT, params=params, timeout=GEOCODE_TIMEOUT_SECONDS)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Google geocoding request failed: %s", exc)
            raise GeocodingError('Geocoding provider request failed.') from exc
        return resp.json()

    @staticmethod
    def _parse(payload: dict) -> GeocodeResult:
        results = payload.get('results') or []
        status = payload.get('status')
        if status != 'OK' or not results:
            raise GeocodingError(f'No geocoding result (status={status}).')
        top = results[0]
        loc = top['geometry']['location']
        return GeocodeResult(
            latitude=float(loc['lat']),
            longitude=float(loc['lng']),
            formatted_address=top.get('formatted_address', ''),
            place_id=top.get('place_id', ''),
        )

    def geocode(self, address: str) -> GeocodeResult:
        return self._parse(self._request({'address': address}))

    def reverse(self, latitude: float, longitude: float) -> GeocodeResult:
        return self._parse(self._request({'latlng': f'{latitude},{longitude}'}))


class MapboxGeocoder(BaseGeocoder):
    name = 'mapbox'
    ENDPOINT = 'https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json'

    def __init__(self, access_token: str):
        self.access_token = access_token

    def _request(self, query: str) -> dict:
        url = self.ENDPOINT.format(query=requests.utils.quote(query)) # type: ignore
        try:
            resp = requests.get(
                url,
                params={'access_token': self.access_token, 'limit': 1},
                timeout=GEOCODE_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Mapbox geocoding request failed: %s", exc)
            raise GeocodingError('Geocoding provider request failed.') from exc
        return resp.json()

    @staticmethod
    def _parse(payload: dict) -> GeocodeResult:
        features = payload.get('features') or []
        if not features:
            raise GeocodingError('No geocoding result.')
        top = features[0]
        # Mapbox returns [longitude, latitude].
        lng, lat = top['center'][0], top['center'][1]
        return GeocodeResult(
            latitude=float(lat),
            longitude=float(lng),
            formatted_address=top.get('place_name', ''),
            place_id=str(top.get('id', '')),
        )

    def geocode(self, address: str) -> GeocodeResult:
        return self._parse(self._request(address))

    def reverse(self, latitude: float, longitude: float) -> GeocodeResult:
        # Mapbox reverse geocoding expects "lng,lat".
        return self._parse(self._request(f'{longitude},{latitude}'))


def get_geocoder() -> BaseGeocoder:
    """Resolve the configured geocoder from settings (None -> NullGeocoder)."""
    provider = (getattr(settings, 'GEOCODING_PROVIDER', '') or '').lower()
    if provider == 'google':
        key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '') or ''
        if key:
            return GoogleGeocoder(key)
        logger.warning("GEOCODING_PROVIDER=google but GOOGLE_MAPS_API_KEY is unset.")
    elif provider == 'mapbox':
        token = getattr(settings, 'MAPBOX_ACCESS_TOKEN', '') or ''
        if token:
            return MapboxGeocoder(token)
        logger.warning("GEOCODING_PROVIDER=mapbox but MAPBOX_ACCESS_TOKEN is unset.")
    return NullGeocoder()
