"""Pickup windows generated from a laundry's opening hours.

Most laundries never publish BookingSlot rows, so the app offered a fixed
08:00-18:00 list regardless of the laundry's real hours (a laundry closed on
Sunday still received Sunday requests). When a day has no published slots,
the schedule endpoint offers one-hour windows inside that day's opening
hours:

- a holiday override replaces the weekly hours; closed days and vacation
  mode give no windows
- a window starts at least PICKUP_WINDOW_LEAD_MINUTES from now and ends by
  closing time (an overnight shift is offered until midnight)
- each window takes PICKUP_WINDOW_CAPACITY pickups, counted from live orders

Opening hours are Ghana time: they are applied in Africa/Accra explicitly,
never the server's TIME_ZONE (tests run in Django's default America/Chicago).
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from laundries.models.opening_hours import HolidayOverride, OpeningHours

WINDOW = timedelta(hours=1)
GHANA = ZoneInfo('Africa/Accra')


def _hours_for(laundry, day):
    """(opening, closing) for the date, or None when closed."""
    override = HolidayOverride.objects.filter(laundry=laundry, date=day).first()
    if override:
        if override.is_closed or not override.opening_time or not override.closing_time:
            return None
        return override.opening_time, override.closing_time
    hours = OpeningHours.objects.filter(laundry=laundry, day=day.isoweekday()).first()
    if not hours or hours.is_closed:
        return None
    closing = hours.closing_time
    if hours.is_overnight or closing <= hours.opening_time:
        closing = time(23, 59, 59)  # offer until midnight; the next day has its own hours
    return hours.opening_time, closing


def has_opening_hours(laundry):
    return OpeningHours.objects.filter(laundry=laundry).exists()


def generate_pickup_windows(laundry, day, now=None):
    """One-hour pickup windows for `day`, shaped like BookingSlotSerializer."""
    from ordering.models import Order

    if not laundry or getattr(laundry, 'vacation_mode', False) or not getattr(laundry, 'is_active', True):
        return []
    hours = _hours_for(laundry, day)
    if not hours:
        return []
    tz = GHANA
    now = now or timezone.now()
    lead = timedelta(minutes=getattr(settings, 'PICKUP_WINDOW_LEAD_MINUTES', 60))
    capacity = getattr(settings, 'PICKUP_WINDOW_CAPACITY', 5)
    opening = timezone.make_aware(datetime.combine(day, hours[0]), tz)
    closing = timezone.make_aware(datetime.combine(day, hours[1]), tz)

    # Start windows on the hour: 08:30 opening -> first window 09:00.
    start = opening if opening.minute == 0 and opening.second == 0 else (opening + WINDOW).replace(minute=0, second=0, microsecond=0)
    booked = {}
    for pickup in Order.objects.filter(
        laundry=laundry, pickup_date__gte=opening, pickup_date__lt=closing,
    ).exclude(status__in=['CANCELLED', 'REJECTED']).values_list('pickup_date', flat=True):
        slot = pickup.replace(minute=0, second=0, microsecond=0)
        booked[slot] = booked.get(slot, 0) + 1

    windows = []
    while start + WINDOW <= closing + timedelta(seconds=1):
        if start >= now + lead:
            taken = booked.get(start, 0)
            windows.append({
                'id': f'hours-{start.strftime("%Y%m%dT%H%M")}',
                'start_time': start.isoformat(),
                'end_time': (start + WINDOW).isoformat(),
                'is_available': taken < capacity,
                'max_bookings': capacity,
                'current_bookings': taken,
                'source': 'opening_hours',
            })
        start += WINDOW
    return windows
