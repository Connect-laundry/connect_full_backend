"""Google place search for the app, proxied with a server-only key.

The app's Google key is restricted to the native Maps SDK, and Google
refuses web-service calls made with it (REQUEST_DENIED), so address search
fell back to OpenStreetMap. This proxy calls Places API (New) with
GOOGLE_PLACES_API_KEY, which never ships in the app, and returns 503 when it
is not configured so the app keeps using its OSM search.

    GET /api/v1/places/autocomplete/?q=KNUST&lat=6.67&lng=-1.57&session=<uuid>
    GET /api/v1/places/details/<place_id>/?session=<uuid>
"""
import hashlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.throttling import GeneralThrottle, UserThrottle

logger = logging.getLogger(__name__)

AUTOCOMPLETE_URL = 'https://places.googleapis.com/v1/places:autocomplete'
DETAILS_URL = 'https://places.googleapis.com/v1/places/{place_id}'
TIMEOUT_S = 6
GHANA_CENTER = (7.95, -1.02)


class PlacesThrottle(UserThrottle):
    scope = 'places'


def _key():
    return getattr(settings, 'GOOGLE_PLACES_API_KEY', '') or ''


def _unconfigured():
    return Response({'detail': 'Place search is not configured.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PlaceAutocompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [GeneralThrottle, PlacesThrottle]

    def get(self, request):
        key = _key()
        if not key:
            return _unconfigured()
        query = (request.query_params.get('q') or '').strip()[:120]
        if len(query) < 2:
            return Response({'status': 'success', 'data': []})
        lat = _float(request.query_params.get('lat'), GHANA_CENTER[0])
        lng = _float(request.query_params.get('lng'), GHANA_CENTER[1])
        session = (request.query_params.get('session') or '')[:64]
        cache_key = 'places:ac:' + hashlib.sha256(f'{query.lower()}|{lat:.2f}|{lng:.2f}'.encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return Response({'status': 'success', 'data': cached})
        body = {
            'input': query,
            'includedRegionCodes': ['gh'],
            'languageCode': 'en',
            'locationBias': {'circle': {'center': {'latitude': lat, 'longitude': lng}, 'radius': 50000.0}},
        }
        if session:
            body['sessionToken'] = session
        try:
            response = requests.post(AUTOCOMPLETE_URL, json=body, timeout=TIMEOUT_S, headers={'X-Goog-Api-Key': key})
        except requests.RequestException as exc:
            logger.warning('Places autocomplete unreachable', extra={'error_type': type(exc).__name__})
            return Response({'detail': 'Place search is unavailable.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if response.status_code != 200:
            logger.error('Places autocomplete rejected: HTTP %s', response.status_code)
            return Response({'detail': 'Place search is unavailable.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        results = []
        for suggestion in (response.json() or {}).get('suggestions', []):
            prediction = suggestion.get('placePrediction') or {}
            fmt = prediction.get('structuredFormat') or {}
            if not prediction.get('placeId'):
                continue
            results.append({
                'place_id': prediction['placeId'],
                'main_text': (fmt.get('mainText') or {}).get('text') or (prediction.get('text') or {}).get('text', ''),
                'secondary_text': (fmt.get('secondaryText') or {}).get('text', ''),
                'description': (prediction.get('text') or {}).get('text', ''),
            })
        cache.set(cache_key, results, 60 * 60 * 24)
        return Response({'status': 'success', 'data': results})


class PlaceDetailsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [GeneralThrottle, PlacesThrottle]

    def get(self, request, place_id):
        key = _key()
        if not key:
            return _unconfigured()
        place_id = place_id[:256]
        cache_key = 'places:det:' + hashlib.sha256(place_id.encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return Response({'status': 'success', 'data': cached})
        params = {'languageCode': 'en'}
        session = (request.query_params.get('session') or '')[:64]
        if session:
            params['sessionToken'] = session
        try:
            response = requests.get(
                DETAILS_URL.format(place_id=requests.utils.quote(place_id, safe='')),
                params=params, timeout=TIMEOUT_S,
                headers={'X-Goog-Api-Key': key, 'X-Goog-FieldMask': 'id,displayName,shortFormattedAddress,formattedAddress,location'},
            )
        except requests.RequestException as exc:
            logger.warning('Places details unreachable', extra={'error_type': type(exc).__name__})
            return Response({'detail': 'Place search is unavailable.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if response.status_code != 200:
            logger.error('Places details rejected: HTTP %s', response.status_code)
            return Response({'detail': 'This place could not be found.'}, status=status.HTTP_404_NOT_FOUND if response.status_code == 404 else status.HTTP_503_SERVICE_UNAVAILABLE)
        place = response.json() or {}
        location = place.get('location') or {}
        if 'latitude' not in location or 'longitude' not in location:
            return Response({'detail': 'This place has no location.'}, status=status.HTTP_404_NOT_FOUND)
        data = {
            'place_id': place.get('id', place_id),
            'name': (place.get('displayName') or {}).get('text', ''),
            'address': place.get('shortFormattedAddress') or place.get('formattedAddress') or '',
            'latitude': location['latitude'],
            'longitude': location['longitude'],
        }
        # Google allows caching place coordinates for up to 30 days.
        cache.set(cache_key, data, 60 * 60 * 24 * 30)
        return Response({'status': 'success', 'data': data})
