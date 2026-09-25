"""Signals for laundry cache invalidation and state synchronization."""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from laundries.models.laundry import Laundry
from laundries.models.opening_hours import OpeningHours, HolidayOverride
from laundries.services.opening_status import invalidate_laundry_status_cache


@receiver([post_save, post_delete], sender=OpeningHours)
def invalidate_on_hours_change(sender, instance, **kwargs):
    if instance.laundry_id:
        invalidate_laundry_status_cache(instance.laundry_id)


@receiver([post_save, post_delete], sender=HolidayOverride)
def invalidate_on_holiday_change(sender, instance, **kwargs):
    if instance.laundry_id:
        invalidate_laundry_status_cache(instance.laundry_id)


@receiver(post_save, sender=Laundry)
def invalidate_on_laundry_change(sender, instance, **kwargs):
    if instance.id:
        invalidate_laundry_status_cache(instance.id)
