"""Pickup times from the laundry's opening hours (no published slots)."""
from datetime import date, datetime, time, timedelta, timezone as dt_tz

import pytest
from django.urls import reverse

from laundries.models.opening_hours import HolidayOverride, OpeningHours
from laundries.services.pickup_windows import generate_pickup_windows
from ordering.models import BookingSlot
from test_booking_create import _auth_client, _build_booking_catalog

MONDAY = date(2030, 1, 7)  # isoweekday 1
BEFORE_OPENING = datetime(2030, 1, 7, 6, 0, tzinfo=dt_tz.utc)


def _catalog():
    from users.models import User
    laundry = _build_booking_catalog()[1]
    customer = User.objects.create_user(email='windows-customer@example.com', phone='233555901099', password='StrongPass123!')
    return customer, laundry


def _hours(laundry, day=1, opening=time(8, 30), closing=time(17, 0), **kw):
    return OpeningHours.objects.create(laundry=laundry, day=day, opening_time=opening, closing_time=closing, **kw)


@pytest.mark.django_db
def test_windows_follow_opening_hours_on_the_hour():
    _, laundry = _catalog()
    _hours(laundry)
    starts = [w['start_time'][11:16] for w in generate_pickup_windows(laundry, MONDAY, now=BEFORE_OPENING)]
    assert starts == ['09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00']  # 16:00-17:00 ends at closing


@pytest.mark.django_db
def test_lead_time_and_closed_days():
    _, laundry = _catalog()
    _hours(laundry)
    _hours(laundry, day=7, is_closed=True)
    at_ten = datetime(2030, 1, 7, 10, 0, tzinfo=dt_tz.utc)
    assert generate_pickup_windows(laundry, MONDAY, now=at_ten)[0]['start_time'][11:16] == '11:00'  # 60 min notice
    assert generate_pickup_windows(laundry, date(2030, 1, 13), now=BEFORE_OPENING) == []  # Sunday closed
    assert generate_pickup_windows(laundry, date(2030, 1, 8), now=BEFORE_OPENING) == []  # no Tuesday hours


@pytest.mark.django_db
def test_holiday_override_and_vacation_mode():
    _, laundry = _catalog()
    _hours(laundry)
    HolidayOverride.objects.create(laundry=laundry, date=MONDAY, opening_time=time(10, 0), closing_time=time(12, 0))
    assert [w['start_time'][11:16] for w in generate_pickup_windows(laundry, MONDAY, now=BEFORE_OPENING)] == ['10:00', '11:00']
    HolidayOverride.objects.filter(laundry=laundry).update(is_closed=True)
    assert generate_pickup_windows(laundry, MONDAY, now=BEFORE_OPENING) == []
    HolidayOverride.objects.all().delete()
    laundry.vacation_mode = True
    assert generate_pickup_windows(laundry, MONDAY, now=BEFORE_OPENING) == []


@pytest.mark.django_db
def test_full_window_is_marked_unavailable(settings):
    from ordering.models import Order
    settings.PICKUP_WINDOW_CAPACITY = 1
    customer, laundry = _catalog()
    _hours(laundry)
    Order.objects.create(user=customer, laundry=laundry, pickup_date=datetime(2030, 1, 7, 9, 15, tzinfo=dt_tz.utc),
                         pickup_address='a', delivery_address='a', total_amount=10)
    nine = generate_pickup_windows(laundry, MONDAY, now=BEFORE_OPENING)[0]
    assert (nine['start_time'][11:16], nine['is_available'], nine['current_bookings']) == ('09:00', False, 1)


@pytest.mark.django_db
def test_schedule_endpoint_uses_hours_unless_slots_are_published():
    customer, laundry = _catalog()
    tomorrow = (datetime.now(dt_tz.utc) + timedelta(days=1)).date()
    _hours(laundry, day=tomorrow.isoweekday(), opening=time(8, 0), closing=time(10, 0))
    client = _auth_client(customer)
    body = client.get(reverse('booking-schedule'), {'laundry_id': str(laundry.id), 'date': tomorrow.isoformat()}).json()
    rows = body.get('data', body)
    assert [r['start_time'][11:16] for r in rows] == ['08:00', '09:00']
    start = datetime.combine(tomorrow, time(14, 0), tzinfo=dt_tz.utc)
    BookingSlot.objects.create(laundry=laundry, start_time=start, end_time=start + timedelta(hours=1))
    body = client.get(reverse('booking-schedule'), {'laundry_id': str(laundry.id), 'date': tomorrow.isoformat()}).json()
    rows = body.get('data', body)
    assert [datetime.fromisoformat(r['start_time']) for r in rows] == [start], 'published slots take priority'


@pytest.mark.django_db
def test_empty_day_says_why():
    customer, laundry = _catalog()
    client = _auth_client(customer)
    tomorrow = (datetime.now(dt_tz.utc) + timedelta(days=1)).date()
    body = client.get(reverse('booking-schedule'), {'laundry_id': str(laundry.id), 'date': tomorrow.isoformat()}).json()
    assert body['data'] == [] and body['reason'] == 'no_hours'
    _hours(laundry, day=tomorrow.isoweekday(), is_closed=True)
    body = client.get(reverse('booking-schedule'), {'laundry_id': str(laundry.id), 'date': tomorrow.isoformat()}).json()
    assert body['reason'] == 'closed_or_full'
