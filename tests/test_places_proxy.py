"""Google place search proxy (server-only key; the app falls back to OSM on 503)."""
from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from users.models import User


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def client(db):
    cache.clear()
    user = User.objects.create_user(email='places@example.com', phone='233200000990', password='x-Pass-12345')
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_unconfigured_returns_503_so_the_app_uses_osm(client, settings):
    settings.GOOGLE_PLACES_API_KEY = ''
    assert client.get('/api/v1/places/autocomplete/', {'q': 'KNUST'}).status_code == 503
    assert client.get('/api/v1/places/details/abc/').status_code == 503


@pytest.mark.django_db
def test_requires_sign_in(settings):
    settings.GOOGLE_PLACES_API_KEY = 'server-key'
    assert APIClient().get('/api/v1/places/autocomplete/', {'q': 'KNUST'}).status_code in (401, 403)


@pytest.mark.django_db
def test_autocomplete_is_ghana_biased_keyed_server_side_and_cached(client, settings):
    settings.GOOGLE_PLACES_API_KEY = 'server-key'
    google = {'suggestions': [{'placePrediction': {'placeId': 'ChIJknust', 'text': {'text': 'KNUST, Kumasi, Ghana'},
        'structuredFormat': {'mainText': {'text': 'KNUST'}, 'secondaryText': {'text': 'Kumasi, Ghana'}}}}]}
    with patch('laundries.views.places.requests.post', return_value=_Resp(200, google)) as post:
        first = client.get('/api/v1/places/autocomplete/', {'q': 'KNUST', 'lat': '6.67', 'lng': '-1.57', 'session': 's1'}).json()
        second = client.get('/api/v1/places/autocomplete/', {'q': 'knust', 'lat': '6.67', 'lng': '-1.57'}).json()
    assert first['data'] == [{'place_id': 'ChIJknust', 'main_text': 'KNUST', 'secondary_text': 'Kumasi, Ghana', 'description': 'KNUST, Kumasi, Ghana'}]
    assert second['data'] == first['data']
    post.assert_called_once()  # second answer from cache
    body, headers = post.call_args.kwargs['json'], post.call_args.kwargs['headers']
    assert body['includedRegionCodes'] == ['gh'] and body['sessionToken'] == 's1'
    assert headers['X-Goog-Api-Key'] == 'server-key'
    assert 'server-key' not in str(first)


@pytest.mark.django_db
def test_details_return_exact_coordinates(client, settings):
    settings.GOOGLE_PLACES_API_KEY = 'server-key'
    google = {'id': 'ChIJknust', 'displayName': {'text': 'KNUST'}, 'shortFormattedAddress': 'KNUST, Kumasi',
              'location': {'latitude': 6.6745, 'longitude': -1.5716}}
    with patch('laundries.views.places.requests.get', return_value=_Resp(200, google)):
        data = client.get('/api/v1/places/details/ChIJknust/', {'session': 's1'}).json()['data']
    assert data == {'place_id': 'ChIJknust', 'name': 'KNUST', 'address': 'KNUST, Kumasi', 'latitude': 6.6745, 'longitude': -1.5716}


@pytest.mark.django_db
def test_google_rejection_is_a_503_not_a_crash(client, settings):
    settings.GOOGLE_PLACES_API_KEY = 'bad'
    with patch('laundries.views.places.requests.post', return_value=_Resp(403, {'error': {}})):
        assert client.get('/api/v1/places/autocomplete/', {'q': 'KNUST'}).status_code == 503
