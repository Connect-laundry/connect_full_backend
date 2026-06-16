"""Tests for operating hours validation, overnight support, and default template."""
import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.models.opening_hours import OpeningHours
from laundries.services.opening_status import is_laundry_open_now
from users.models import User


def _owner(email='owner-oh@example.com', phone='233500050001'):
    return User.objects.create_user(
        email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _base_payload():
    return {
        'name': 'Hours Laundry',
        'address': '5 Ring Road, Accra',
        'city': 'Accra',
        'latitude': '5.603700',
        'longitude': '-0.187000',
        'phone_number': '0240000050',
        'price_range': '$$',
    }


LIST_URL = 'dashboard-my-laundry'
DETAIL_URL = 'dashboard-my-laundry-detail'
TEMPLATE_URL = 'dashboard-hours-template'


@pytest.mark.django_db
class TestOvernightHoursValidation:
    """Overnight hours span midnight (e.g. 20:00 → 02:00)."""

    def test_overnight_closing_before_opening_is_valid(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 6, 'opening_time': '20:00', 'closing_time': '02:00',
             'is_closed': False, 'is_overnight': True},
        ]
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        oh = OpeningHours.objects.get(laundry__name='Hours Laundry', day=6)
        assert oh.is_overnight is True
        assert oh.opening_time.strftime('%H:%M') == '20:00'
        assert oh.closing_time.strftime('%H:%M') == '02:00'

    def test_overnight_equal_times_rejected(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 6, 'opening_time': '22:00', 'closing_time': '22:00',
             'is_closed': False, 'is_overnight': True},
        ]
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert Laundry.objects.count() == 0  # rolled back


@pytest.mark.django_db
class TestNormalHoursValidation:
    def test_closing_before_opening_without_overnight_rejected(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 1, 'opening_time': '18:00', 'closing_time': '08:00',
             'is_closed': False, 'is_overnight': False},
        ]
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert Laundry.objects.count() == 0

    def test_equal_times_without_overnight_rejected(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 1, 'opening_time': '10:00', 'closing_time': '10:00',
             'is_closed': False},
        ]
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_open_day_without_times_rejected(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 1, 'is_closed': False},
        ]
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestClosedDayHandling:
    def test_closed_day_without_times_defaults_to_midnight(self):
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 7, 'is_closed': True},
        ]
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        oh = OpeningHours.objects.get(laundry__name='Hours Laundry', day=7)
        assert oh.is_closed is True
        assert oh.opening_time.strftime('%H:%M') == '00:00'
        assert oh.closing_time.strftime('%H:%M') == '00:00'
        assert oh.is_overnight is False

    def test_closed_day_with_times_still_stores_midnight(self):
        """Even if times are sent for a closed day, they default to midnight."""
        client = _client(_owner())
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 7, 'opening_time': '10:00', 'closing_time': '20:00', 'is_closed': True},
        ]
        resp = client.post(reverse(LIST_URL), payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        oh = OpeningHours.objects.get(laundry__name='Hours Laundry', day=7)
        assert oh.is_closed is True


@pytest.mark.django_db
class TestOpeningStatus:
    def test_open_status_uses_closed_days_overnight_and_vacation_mode(self):
        owner = _owner()
        laundry = Laundry.objects.create(
            name='Status Laundry',
            address='1 Status St',
            latitude='5.603700',
            longitude='-0.187000',
            phone_number='0240000051',
            owner=owner,
            is_active=True,
            status=Laundry.ApprovalStatus.APPROVED,
        )
        OpeningHours.objects.create(
            laundry=laundry,
            day=2,
            opening_time=time(20, 0),
            closing_time=time(2, 0),
            is_overnight=True,
        )
        OpeningHours.objects.create(
            laundry=laundry,
            day=3,
            opening_time=time(0, 0),
            closing_time=time(0, 0),
            is_closed=True,
        )
        laundry.refresh_from_db()
        assert laundry.is_active is True
        assert laundry.vacation_mode is False
        assert [
            (hour.day, hour.opening_time, hour.closing_time, hour.is_closed, hour.is_overnight)
            for hour in laundry.opening_hours.all()
        ] == [
            (2, time(20, 0), time(2, 0), False, True),
            (3, time(0, 0), time(0, 0), True, False),
        ]
        assert is_laundry_open_now(laundry, datetime(2026, 6, 17, 2, 0, tzinfo=ZoneInfo('UTC'))) is True
        assert is_laundry_open_now(laundry, datetime(2026, 6, 17, 6, 0, tzinfo=ZoneInfo('UTC'))) is True
        assert is_laundry_open_now(laundry, datetime(2026, 6, 17, 17, 0, tzinfo=ZoneInfo('UTC'))) is False

        laundry.vacation_mode = True
        assert is_laundry_open_now(laundry, datetime(2026, 6, 17, 2, 0, tzinfo=ZoneInfo('UTC'))) is False


@pytest.mark.django_db
class TestHoursDefaultTemplate:
    def test_template_creates_seven_days(self):
        owner = _owner()
        client = _client(owner)
        # Create laundry first
        resp = client.post(reverse(LIST_URL), _base_payload(), format='json')
        assert resp.status_code == status.HTTP_201_CREATED

        # Apply default template
        resp = client.post(reverse(TEMPLATE_URL))
        assert resp.status_code == status.HTTP_200_OK, resp.data
        laundry = Laundry.objects.get(owner=owner)
        assert OpeningHours.objects.filter(laundry=laundry).count() == 7

        # Check defaults: Mon-Fri 08-18, Sat 09-15, Sun closed
        mon = OpeningHours.objects.get(laundry=laundry, day=1)
        assert mon.opening_time.strftime('%H:%M') == '08:00'
        assert mon.closing_time.strftime('%H:%M') == '18:00'
        assert mon.is_closed is False

        sat = OpeningHours.objects.get(laundry=laundry, day=6)
        assert sat.opening_time.strftime('%H:%M') == '09:00'
        assert sat.closing_time.strftime('%H:%M') == '15:00'

        sun = OpeningHours.objects.get(laundry=laundry, day=7)
        assert sun.is_closed is True

    def test_template_without_laundry_returns_error(self):
        resp = _client(_owner()).post(reverse(TEMPLATE_URL))
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestHoursUpsert:
    def test_patch_replaces_hours_set(self):
        """Sending a subset of days removes unlisted days."""
        owner = _owner()
        client = _client(owner)
        payload = _base_payload()
        payload['operating_hours'] = [
            {'day': 1, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
            {'day': 2, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
            {'day': 3, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
        ]
        created = client.post(reverse(LIST_URL), payload, format='json')
        assert created.status_code == status.HTTP_201_CREATED
        lid = created.data['data']['id']
        assert OpeningHours.objects.filter(laundry_id=lid).count() == 3

        # Patch with only Monday → Tuesday and Wednesday removed
        resp = client.patch(
            reverse(DETAIL_URL, kwargs={'id': lid}),
            {'operating_hours': [
                {'day': 1, 'opening_time': '09:00', 'closing_time': '17:00', 'is_closed': False},
            ]},
            format='json',
        )
        assert resp.status_code == status.HTTP_200_OK
        assert OpeningHours.objects.filter(laundry_id=lid).count() == 1
        mon = OpeningHours.objects.get(laundry_id=lid, day=1)
        assert mon.opening_time.strftime('%H:%M') == '09:00'

    def test_multipart_json_hours_string(self):
        """Operating hours sent as JSON string in multipart requests."""
        owner = _owner()
        client = _client(owner)
        payload = _base_payload()
        payload['operating_hours'] = json.dumps([
            {'day': 1, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
        ])
        resp = client.post(reverse(LIST_URL), payload, format='multipart')
        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert OpeningHours.objects.count() == 1
