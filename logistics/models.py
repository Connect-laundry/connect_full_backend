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
        return f"{self.assignment_type} - Order {self.order.order_no} - {self.driver.email}"

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
        return f"{self.order.order_no} - {self.status} @ {self.timestamp}"
