"""Nearby search without PostGIS (production).

Production 2026-09-22: USE_POSTGIS is off, so ?nearby=true ignored the radius.
A search at KNUST returned an Accra laundry about 250 km away, and every card
said 'N/A away' because distance was always null.
"""
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from laundries.models import Laundry
from users.models import User


@pytest.fixture
def laundries(db):
    owner = User.objects.create_user(email='owner-near@example.com', password='x-Pass-12345', phone='233200000980', role='OWNER')
    make = lambda name, lat, lng: Laundry.objects.create(
        name=name, description='d', address='a', latitude=lat, longitude=lng, owner=owner,
        is_active=True, status=Laundry.ApprovalStatus.APPROVED)
    return {
        'sunset': make('Sunset', 6.673408, -1.567431),       # ~0.6 km from KNUST
        'connect': make('Connect', 6.690339, -1.548032),     # ~3.2 km
        'accra': make('Accra', 5.603700, -0.187000),         # ~250 km
    }


def _results(response):
    body = response.json()
    data = body.get('data', body)
    return data.get('results', data) if isinstance(data, dict) else data


@pytest.mark.django_db
def test_nearby_filters_by_radius_orders_by_distance_and_reports_km(laundries):
    response = APIClient().get(reverse('laundry-list'), {'nearby': 'true', 'lat': '6.6745', 'lng': '-1.5716', 'radius': '10'})
    assert response.status_code == 200
    rows = _results(response)
    assert [r['name'] for r in rows] == ['Sunset', 'Connect'], 'Accra (250 km) must be excluded'
    assert 0.3 < rows[0]['distance'] < 1.0
    assert 2.5 < rows[1]['distance'] < 4.0


@pytest.mark.django_db
def test_radius_widens_and_bad_coordinates_are_ignored(laundries):
    wide = _results(APIClient().get(reverse('laundry-list'), {'nearby': 'true', 'lat': '6.6745', 'lng': '-1.5716', 'radius': '100'}))
    assert {r['name'] for r in wide} == {'Sunset', 'Connect'}  # still under the 100 km cap
    bad = APIClient().get(reverse('laundry-list'), {'nearby': 'true', 'lat': 'abc', 'lng': '-1.5716'})
    assert bad.status_code == 200
    assert len(_results(bad)) == 3  # no filter applied rather than a 500


@pytest.mark.django_db
def test_top_rated_and_open_now_chips_filter_on_the_server(laundries):
    """The Discovery chips send min_rating / open_now: prove the server applies both."""
    from datetime import time
    from laundries.models import OpeningHours
    from laundries.models.review import Review
    reviewer = User.objects.create_user(email='reviewer@example.com', password='x-Pass-12345', phone='233200000981')
    Review.objects.create(laundry=laundries['sunset'], user=reviewer, rating=5, comment='great')
    Review.objects.create(laundry=laundries['connect'], user=reviewer, rating=3, comment='ok')
    for day in range(1, 8):
        OpeningHours.objects.create(laundry=laundries['sunset'], day=day, opening_time=time(0, 0), closing_time=time(23, 59, 59))
        OpeningHours.objects.create(laundry=laundries['connect'], day=day, opening_time=time(0, 0), closing_time=time(0, 0, 1))
        OpeningHours.objects.create(laundry=laundries['accra'], day=day, opening_time=time(0, 0), closing_time=time(0, 0, 1))
    client = APIClient()
    rated = _results(client.get(reverse('laundry-list'), {'min_rating': '4.5'}))
    assert [r['name'] for r in rated] == ['Sunset']
    open_now = _results(client.get(reverse('laundry-list'), {'open_now': 'true'}))
    assert [r['name'] for r in open_now] == ['Sunset']
    assert all(r['isOpen'] for r in open_now), 'filter and card badge use the same rule'


@pytest.mark.django_db
def test_list_exposes_is_featured_for_the_map_filter(laundries):
    Laundry.objects.filter(pk=laundries['sunset'].pk).update(is_featured=True)
    rows = {r['name']: r for r in _results(APIClient().get(reverse('laundry-list')))}
    assert rows['Sunset']['isFeatured'] is True
    assert rows['Connect']['isFeatured'] is False
