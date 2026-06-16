"""Backend source of truth for laundry open/closed status."""
from django.utils import timezone

from laundries.models.laundry import Laundry
from laundries.models.opening_hours import HolidayOverride, OpeningHours


def _time_in_range(opening_time, closing_time, current_time):
    if not opening_time or not closing_time:
        return False
    if closing_time < opening_time:
        return current_time >= opening_time or current_time <= closing_time
    return opening_time <= current_time <= closing_time


def _hours_match(opening_hours, current_time):
    if not opening_hours or opening_hours.is_closed:
        return False
    if opening_hours.is_overnight:
        return current_time >= opening_hours.opening_time or current_time <= opening_hours.closing_time
    return _time_in_range(opening_hours.opening_time, opening_hours.closing_time, current_time)


def is_laundry_open_now(laundry, now=None):
    """Return whether a laundry is open using holiday overrides, hours, and vacation mode."""
    if not laundry or getattr(laundry, "vacation_mode", False) or not getattr(laundry, "is_active", True):
        return False

    local_now = timezone.localtime(now or timezone.now())
    current_date = local_now.date()
    current_time = local_now.time()
    current_day = current_date.isoweekday()

    override = HolidayOverride.objects.filter(laundry=laundry, date=current_date).first()
    if override:
        if override.is_closed:
            return False
        return _time_in_range(override.opening_time, override.closing_time, current_time)

    opening_hours_manager = getattr(laundry, "opening_hours", None)
    if opening_hours_manager is not None:
        hours = list(opening_hours_manager.all())
    else:
        hours = list(OpeningHours.objects.filter(laundry=laundry))

    today_hours = next((hour for hour in hours if hour.day == current_day), None)
    if _hours_match(today_hours, current_time):
        return True

    yesterday_day = 7 if current_day == 1 else current_day - 1
    yesterday_hours = next((hour for hour in hours if hour.day == yesterday_day and hour.is_overnight), None)
    return _hours_match(yesterday_hours, current_time)


def get_open_laundry_ids(now=None):
    """Return approved active laundry ids that are open right now."""
    laundries = (
        Laundry.objects.filter(
            status=Laundry.ApprovalStatus.APPROVED,
            is_active=True,
            vacation_mode=False,
        )
        .prefetch_related("opening_hours")
        .only("id", "is_active", "vacation_mode", "status")
    )
    return {laundry.id for laundry in laundries if is_laundry_open_now(laundry, now=now)}
