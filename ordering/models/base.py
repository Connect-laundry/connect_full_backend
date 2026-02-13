import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

class LaunderableItem(models.Model):
    """Global catalog of items that can be laundered (e.g., Shirt, Trouser)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey('laundries.Category', on_delete=models.CASCADE, related_name='launderable_items')
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to='items/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Launderable Item')
        verbose_name_plural = _('Launderable Items')
        ordering = ['name']

    def __str__(self):
        return self.name

class Order(models.Model):
    """Main order record tracking the lifecycle of a laundry request."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        PICKED_UP = 'PICKED_UP', _('Picked Up')
        WASHING = 'WASHING', _('Washing')
        READY = 'READY', _('Ready for Delivery')
        DELIVERED = 'DELIVERED', _('Delivered')
        CANCELLED = 'CANCELLED', _('Cancelled')

    class PaymentStatus(models.TextChoices):
        PAID = 'PAID', _('Paid')
        UNPAID = 'UNPAID', _('Unpaid')
        REFUNDED = 'REFUNDED', _('Refunded')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_no = models.CharField(max_length=20, unique=True, editable=False)
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    laundry = models.ForeignKey('laundries.Laundry', on_delete=models.CASCADE, related_name='orders')
    service_type = models.ForeignKey('laundries.Category', on_delete=models.SET_NULL, null=True, related_name='orders')
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    pickup_date = models.DateTimeField()
    delivery_date = models.DateTimeField(null=True, blank=True)
    
    address = models.TextField()
    special_instructions = models.TextField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order_no']),
            models.Index(fields=['status']),
            models.Index(fields=['user', 'created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.order_no:
            # pyre-ignore[assignment]
            self.order_no = f"CN-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order_no} ({self.status})"

class OrderItem(models.Model):
    """Line items for a specific order."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(LaunderableItem, on_delete=models.SET_NULL, null=True)
    
    # Snapshot fields to preserve history if catalog items change
    name = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity} x {self.name} ({self.order.order_no})"

class BookingSlot(models.Model):
    """Available delivery/pickup windows for a laundry store."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey('laundries.Laundry', on_delete=models.CASCADE, related_name='booking_slots')
    
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    
    is_available = models.BooleanField(default=True)
    max_bookings = models.PositiveIntegerField(default=5)
    current_bookings = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ['laundry', 'start_time', 'end_time']
        ordering = ['start_time']

    def __str__(self):
        return f"{self.laundry.name}: {self.start_time.strftime('%Y-%m-%d %H:%M')}"
