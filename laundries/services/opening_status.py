"""Backend source of truth for laundry open/closed status.

Ghana operates on Africa/Accra (UTC+0, no daylight saving time).
Operating hours and holiday overrides are evaluated strictly in Ghana time,
independent of server host or test environment timezones.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.core.cache import cache

from laundries.models.laundry import Laundry
from laundries.models.opening_hours import HolidayOverride, OpeningHours

GHANA_TZ = ZoneInfo("Africa/Accra")


def _normalize_now(now=None) -> datetime:
    """Normalize datetime to Ghana time (Africa/Accra)."""
    if now is None:
        return timezone.now().astimezone(GHANA_TZ)
    if timezone.is_naive(now):
        return timezone.make_aware(now, GHANA_TZ)
    return now.astimezone(GHANA_TZ)


def is_laundry_open_now(laundry, now=None) -> bool:
    """
    Return whether a laundry is currently open according to its operating schedule.
    
    Checks:
    1. Active status & vacation mode
    2. Today's holiday overrides (closed flag, special hours, overnight)
    3. Today's regular operating hours
    4. Yesterday's overnight shift spilling into today's early morning
    """
    if not laundry or getattr(laundry, "vacation_mode", False) or not getattr(laundry, "is_active", True):
        return False

    local_now = _normalize_now(now)
    current_date = local_now.date()
    current_time = local_now.time()
    current_day = current_date.isoweekday()

    # Pre-fetch or read hours
    opening_hours_manager = getattr(laundry, "opening_hours", None)
    if opening_hours_manager is not None:
        hours = list(opening_hours_manager.all())
    else:
        hours = list(OpeningHours.objects.filter(laundry=laundry))

    # --- 1. Check Today's Hours & Overrides ---
    today_override = HolidayOverride.objects.filter(laundry=laundry, date=current_date).first()
    if today_override:
        if today_override.is_closed or not today_override.opening_time or not today_override.closing_time:
            today_open = False
        elif today_override.opening_time == today_override.closing_time:
            # 24-hour schedule on override
            return True
        elif today_override.closing_time < today_override.opening_time:
            # Overnight override: today's portion is from opening_time until midnight
            if current_time >= today_override.opening_time:
                return True
            today_open = False
        else:
            if today_override.opening_time <= current_time <= today_override.closing_time:
                return True
            today_open = False
    else:
        today_hours = next((hour for hour in hours if hour.day == current_day), None)
        if today_hours and not today_hours.is_closed and today_hours.opening_time and today_hours.closing_time:
            if today_hours.opening_time == today_hours.closing_time:
                # 24-hour schedule
                return True
            elif today_hours.closing_time < today_hours.opening_time:
                # True overnight: crosses midnight (e.g. 20:00 -> 02:00)
                # Today's portion is opening_time through 23:59:59
                if current_time >= today_hours.opening_time:
                    return True
            else:
                # Standard same-day schedule (e.g. 08:30 -> 22:00).
                # Note: Even if is_overnight was set in DB, closing > opening is physically same-day.
                if today_hours.opening_time <= current_time <= today_hours.closing_time:
                    return True

    # --- 2. Check Yesterday's Overnight Spillover ---
    yesterday_date = current_date - timedelta(days=1)
    yesterday_day = 7 if current_day == 1 else current_day - 1

    yesterday_override = HolidayOverride.objects.filter(laundry=laundry, date=yesterday_date).first()
    if yesterday_override:
        if (
            not yesterday_override.is_closed
            and yesterday_override.opening_time
            and yesterday_override.closing_time
            and yesterday_override.closing_time < yesterday_override.opening_time
        ):
            if current_time <= yesterday_override.closing_time:
                return True
    else:
        yesterday_hours = next(
            (hour for hour in hours if hour.day == yesterday_day and hour.closing_time and hour.opening_time and hour.closing_time < hour.opening_time),
            None
        )
        if yesterday_hours and not yesterday_hours.is_closed and yesterday_hours.closing_time:
            if current_time <= yesterday_hours.closing_time:
                return True

    return False


def get_next_open_at(laundry, now=None) -> datetime | None:
    """Return the next datetime the laundry will open, or None if unknown/closed long-term."""
    if not laundry or getattr(laundry, "vacation_mode", False) or not getattr(laundry, "is_active", True):
        return None

    local_now = _normalize_now(now)
    current_date = local_now.date()
    current_time = local_now.time()

    opening_hours_manager = getattr(laundry, "opening_hours", None)
    if opening_hours_manager is not None:
        hours = list(opening_hours_manager.all())
    else:
        hours = list(OpeningHours.objects.filter(laundry=laundry))

    overrides = {
        ho.date: ho
        for ho in HolidayOverride.objects.filter(
            laundry=laundry,
            date__gte=current_date,
            date__lte=current_date + timedelta(days=7),
        )
    }

    for day_offset in range(8):
        target_date = current_date + timedelta(days=day_offset)
        override = overrides.get(target_date)

        if override:
            if override.is_closed or not override.opening_time:
                continue
            open_time = override.opening_time
        else:
            day_num = target_date.isoweekday()
            day_hours = next((h for h in hours if h.day == day_num), None)
            if not day_hours or day_hours.is_closed or not day_hours.opening_time:
                continue
            open_time = day_hours.opening_time

        if day_offset == 0:
            if open_time > current_time:
                return datetime.combine(target_date, open_time, tzinfo=GHANA_TZ)
        else:
            return datetime.combine(target_date, open_time, tzinfo=GHANA_TZ)

    return None


def get_laundry_opening_status(laundry, now=None) -> dict:
    """
    Return comprehensive open/closed status for client consumption.
    
    Fields:
      - is_open_now (bool): whether currently open
      - status_as_of (str): ISO timestamp of check
      - next_open_at (str|None): ISO timestamp when business next opens
      - accepts_future_bookings (bool): whether customer can book future slots
      - vacation_mode (bool): whether business is on vacation
    """
    local_now = _normalize_now(now)
    is_open = is_laundry_open_now(laundry, now=local_now)
    vacation = bool(getattr(laundry, "vacation_mode", False))
    is_active = bool(getattr(laundry, "is_active", True))

    # A laundry accepts future bookings as long as it's active and not on vacation,
    # even when currently closed!
    accepts_future_bookings = is_active and not vacation

    next_open = None
    if not is_open and accepts_future_bookings:
        next_dt = get_next_open_at(laundry, now=local_now)
        if next_dt:
            next_open = next_dt.isoformat()

    return {
        "is_open_now": is_open,
        "status_as_of": local_now.isoformat(),
        "next_open_at": next_open,
        "accepts_future_bookings": accepts_future_bookings,
        "vacation_mode": vacation,
    }


def get_open_laundry_ids(now=None) -> set:
    """Return set of laundry IDs that are open right now."""
    laundries = Laundry.objects.filter(is_active=True, vacation_mode=False).prefetch_related("opening_hours")
    return {laundry.id for laundry in laundries if is_laundry_open_now(laundry, now=now)}


def invalidate_laundry_status_cache(laundry_id):
    """Invalidate all cached opening status keys for a laundry."""
    if laundry_id:
        cache.delete(f"laundry_is_open_{laundry_id}")
        cache.delete(f"laundry_opening_status_{laundry_id}")
