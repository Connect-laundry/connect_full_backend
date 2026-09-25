import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

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
    currency = models.CharField(max_length=3, default='GHS', editable=False)
    effective_from = models.DateTimeField(
        help_text=_("When these rates take effect.")
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

    @classmethod
    def get_active(cls):
        """
        Returns the active pricing configuration whose effective_from <= now.
        Returns a fallback disabled config if none is found.
        """
        try:
            from django.utils import timezone
            now = timezone.now()
            config = cls.objects.filter(is_active=True, effective_from__lte=now).order_by('-effective_from', '-created_at').first()
            if not config:
                # Fallback to any active config
                config = cls.objects.filter(is_active=True).order_by('-created_at').first()
            return config
        except Exception:
            return None



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

