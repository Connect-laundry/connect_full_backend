import logging
import time
import uuid
from decimal import Decimal

# pyre-ignore[missing-module]
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

class DeliveryAssignment(models.Model):
    """Linking orders to drivers for pickup or delivery."""
    class AssignmentType(models.TextChoices):
        PICKUP = 'PICKUP', _('Pickup')
        DELIVERY = 'DELIVERY', _('Delivery')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('ordering.Order', on_delete=models.CASCADE, related_name='delivery_assignments')
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, limit_choices_to={'role': 'DRIVER'})
    
    assignment_type = models.CharField(max_length=10, choices=AssignmentType.choices)
    assigned_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    status = models.CharField(max_length=20, default='ASSIGNED') # ASSIGNED, IN_TRANSIT, COMPLETED

    def __str__(self):
        order_no = self.order.order_no if getattr(self, 'order', None) else 'N/A'
        driver_email = self.driver.email if getattr(self, 'driver', None) else 'Unassigned'
        return f"{self.assignment_type} - Order {order_no} - {driver_email}"

class TrackingLog(models.Model):
    """Audit trail of order movements and status changes."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('ordering.Order', on_delete=models.CASCADE, related_name='tracking_logs')
    
    status = models.CharField(max_length=50)
    description = models.TextField(null=True, blank=True)
    
    location_name = models.CharField(max_length=255, null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        order_no = self.order.order_no if getattr(self, 'order', None) else 'N/A'
        return f"{order_no} - {self.status} @ {self.timestamp}"


class LogisticsPricingConfig(models.Model):
    """
    Authoritative backend configuration for pickup and delivery pricing.
    Changes made here in Django Admin immediately govern quotes in the mobile app
    without requiring any new app release.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pricing_enabled = models.BooleanField(
        default=False,
        help_text=_("Turn ON to activate dynamic pickup/delivery pricing. When OFF, mobile shows delivery is separate.")
    )
    pickup_price_per_km = models.DecimalField(
        max_digits=8, decimal_places=2, default=2.50,
        help_text=_("Pickup price per kilometre in GHS.")
    )
    delivery_price_per_km = models.DecimalField(
        max_digits=8, decimal_places=2, default=3.00,
        help_text=_("Delivery price per kilometre in GHS.")
    )
    pickup_base_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0.00,
        help_text=_("Optional base fee for pickup in GHS.")
    )
    delivery_base_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0.00,
        help_text=_("Optional base fee for delivery in GHS.")
    )
    pickup_min_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0.00,
        help_text=_("Optional minimum charge for pickup in GHS.")
    )
    delivery_min_fee = models.DecimalField(
        max_digits=8, decimal_places=2, default=0.00,
        help_text=_("Optional minimum charge for delivery in GHS.")
    )
    max_service_distance_km = models.DecimalField(
        max_digits=6, decimal_places=2, default=25.00,
        help_text=_("Maximum distance in km allowed for logistics service.")
    )
    distance_rounding_precision = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Decimal places for rounding distance (e.g. 1 means 0.1 km / 100m).")
    )
    minimum_billable_distance_km = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        help_text=_("Legs shorter than this are billed as this distance (e.g. 1.00 bills a 400 m trip as 1 km).")
    )
    road_distance_factor = models.DecimalField(
        max_digits=4, decimal_places=2, default=1.00,
        validators=[MinValueValidator(Decimal('1.00')), MaxValueValidator(Decimal('3.00'))],
        help_text=_(
            "Distance is measured in a straight line between the two map pins, then multiplied by this "
            "factor to approximate the road route. 1.00 bills the straight line; 1.30 adds 30%."
        )
    )
    # Oldest app builds whose screens present in-app transport pricing
    # correctly. While pricing is ON, older builds cannot start or place a
    # booking and are asked to update. Build 9 (the launch build) predates it.
    min_android_build = models.PositiveIntegerField(
        default=10,
        help_text=_("Lowest Android build (versionCode) allowed to book while pricing is ON."),
    )
    min_ios_build = models.PositiveIntegerField(
        default=10,
        help_text=_("Lowest iOS build number allowed to book while pricing is ON."),
    )
    currency = models.CharField(max_length=3, default='GHS', editable=False)
    effective_from = models.DateTimeField(
        default=timezone.now,
        help_text=_("When these rates take effect. The newest row already in effect is the one used.")
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this configuration profile is active.")
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text=_("Monotonically increasing version identifier.")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_from', '-created_at']
        verbose_name = _('Logistics Pricing Configuration')
        verbose_name_plural = _('Logistics Pricing Configurations')

    def __str__(self):
        status_str = "ACTIVE" if (self.is_active and self.pricing_enabled) else "DISABLED"
        return f"Logistics Pricing v{self.version} ({status_str}) - Pickup: GHS {self.pickup_price_per_km}/km, Delivery: GHS {self.delivery_price_per_km}/km"

    def clean(self):
        super().clean()
        errors = {}
        for name in (
            'pickup_price_per_km', 'delivery_price_per_km', 'pickup_base_fee',
            'delivery_base_fee', 'pickup_min_fee', 'delivery_min_fee',
            'minimum_billable_distance_km',
        ):
            value = getattr(self, name)
            if value is not None and Decimal(str(value)) < 0:
                errors[name] = _("Cannot be negative.")
        if self.max_service_distance_km is not None and Decimal(str(self.max_service_distance_km)) <= 0:
            errors['max_service_distance_km'] = _("Must be greater than zero.")
        if self.distance_rounding_precision is not None and self.distance_rounding_precision > 3:
            errors['distance_rounding_precision'] = _("Use 0 to 3 decimal places.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Versions are global, not per row, so an order's `logistics_pricing_version`
        # always names exactly one set of rates even when several rows exist.
        if self._state.adding:
            latest = LogisticsPricingConfig.objects.aggregate(m=models.Max('version'))['m'] or 0
            self.version = max(self.version or 0, latest + 1)
        super().save(*args, **kwargs)
        clear_pricing_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        clear_pricing_cache()
        return result

    @classmethod
    def next_version(cls):
        return (cls.objects.aggregate(m=models.Max('version'))['m'] or 0) + 1

    @classmethod
    def get_active(cls):
        """
        The configuration in force right now: the newest active row whose
        `effective_from` has passed. None when there is none, which callers treat
        as "pricing disabled".

        A row scheduled for the future is deliberately not used early.

        Cached per process for a few seconds because order lists price every
        row. A save or delete clears this process's cache at once; other
        workers pick the change up within `_CACHE_TTL_SECONDS`.
        """
        now_mono = time.monotonic()
        cached = _active_cache.get('value', _MISSING)
        if cached is not _MISSING and now_mono - _active_cache.get('at', 0) < _CACHE_TTL_SECONDS:
            return cached
        try:
            config = (
                cls.objects.filter(is_active=True, effective_from__lte=timezone.now())
                .order_by('-effective_from', '-created_at')
                .first()
            )
        except Exception:
            logger.warning("Could not load logistics pricing configuration", exc_info=True)
            return None
        _active_cache['value'] = config
        _active_cache['at'] = now_mono
        return config


_MISSING = object()
_CACHE_TTL_SECONDS = 5.0
_active_cache: dict = {}


def clear_pricing_cache(*args, **kwargs):
    _active_cache.clear()


# Bulk queryset deletes and updates made outside `save()` still clear the cache.
models.signals.post_save.connect(clear_pricing_cache, sender=LogisticsPricingConfig, dispatch_uid='logistics_pricing_cache_save')
models.signals.post_delete.connect(clear_pricing_cache, sender=LogisticsPricingConfig, dispatch_uid='logistics_pricing_cache_delete')



class LogisticsPricingAudit(models.Model):
    """
    Immutable audit history of who modified logistics rates, old vs new values, and timestamp.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    config = models.ForeignKey(LogisticsPricingConfig, on_delete=models.CASCADE, related_name='audit_logs')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-changed_at']
        verbose_name = _('Logistics Pricing Audit')
        verbose_name_plural = _('Logistics Pricing Audits')

    def __str__(self):
        user_email = self.changed_by.email if self.changed_by else 'System'
        return f"Pricing audit {self.config.version} by {user_email} at {self.changed_at}"

