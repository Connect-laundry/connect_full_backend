import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
from laundries.utils.validators import validate_file_upload

class LaunderableItem(models.Model):
    """Global catalog of items that can be laundered (e.g., Shirt, Trouser)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    item_category = models.ForeignKey(
        'laundries.Category', 
        on_delete=models.CASCADE, 
        related_name='launderable_items',
        limit_choices_to={'type': 'ITEM_CATEGORY'},
        null=True,
        blank=True
    )
    image = models.ImageField(upload_to='items/', null=True, blank=True, validators=[validate_file_upload])
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
        CONFIRMED = 'CONFIRMED', _('Confirmed')
        REJECTED = 'REJECTED', _('Rejected')
        PICKED_UP = 'PICKED_UP', _('Picked Up')
        IN_PROCESS = 'IN_PROCESS', _('In Process')
        OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', _('Out for Delivery')
        DELIVERED = 'DELIVERED', _('Delivered')
        COMPLETED = 'COMPLETED', _('Completed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    class PaymentStatus(models.TextChoices):
        PAID = 'PAID', _('Paid')
        UNPAID = 'UNPAID', _('Unpaid')
        REFUNDED = 'REFUNDED', _('Refunded')

    class PricingMode(models.TextChoices):
        # Customer picks itemised services; price is known upfront.
        BY_ITEM = 'BY_ITEM', _('By item')
        # Customer gives an estimated weight; price comes from the laundry's
        # per-kg tariff and is confirmed against the weigh-in at the shop.
        BY_WEIGHT = 'BY_WEIGHT', _('By weight')
        # Customer requests a pickup with no upfront price. The laundry weighs
        # and inspects, then sends an invoice the customer pays in-app. There
        # are no items and no price until that quote arrives.
        CUSTOM_QUOTE = 'CUSTOM_QUOTE', _('Pay after quote')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_no = models.CharField(max_length=20, unique=True, editable=False)
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    laundry = models.ForeignKey('laundries.Laundry', on_delete=models.CASCADE, related_name='orders')
    service_type = models.ForeignKey('laundries.Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)

    # How this order was priced. BY_ITEM is the default so every existing order
    # and every request from older clients keeps its current behaviour.
    pricing_mode = models.CharField(
        max_length=20, choices=PricingMode.choices, default=PricingMode.BY_ITEM
    )
    # Set only for BY_WEIGHT: the weight the customer estimated at booking. The
    # shop may adjust after weighing, which is why it is labelled "estimated".
    estimated_weight_kg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, db_column='final_price')
    coupon = models.ForeignKey(
        'ordering.Coupon', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='orders'
    )
    
    # --- Price snapshot -------------------------------------------------
    # What this order was actually charged, frozen at creation.
    #
    # These used to be recomputed from live laundry prices on every read, so
    # editing a price rewrote the history of every past order and the platform
    # could not prove what it had charged. Money owed to a laundry is settled
    # against these numbers, so they must never move after the fact.
    items_total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    pickup_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=3, default='GHS')
    # Whether logistics were billed in the app when this order was placed.
    delivery_fees_in_app = models.BooleanField(default=False)
    # Null on orders created before snapshots existed; those still recompute.
    priced_at = models.DateTimeField(null=True, blank=True)

    pickup_date = models.DateTimeField()
    delivery_date = models.DateTimeField(null=True, blank=True)
    
    # Dual Address Support
    pickup_address = models.TextField(null=True, blank=True)
    pickup_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    pickup_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    
    delivery_address = models.TextField(null=True, blank=True)
    delivery_lat = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    delivery_lng = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    
    # Legacy field (marking as nullable for migration)
    address = models.TextField(null=True, blank=True)
    special_instructions = models.TextField(null=True, blank=True)
    
    # Transition Timestamps
    confirmed_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    out_for_delivery_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    # --- Delivery handover -----------------------------------------------
    # A short code the customer reads out when the laundry hands their clothes
    # back. The laundry enters it to close the order, which is the only
    # evidence the platform has that a delivery really happened: laundries mark
    # their own orders delivered, and the money is released on that word.
    #
    # Deliberately not a hard requirement. DoorDash's equivalent has a
    # "cannot collect PIN" path because customers are unreachable often enough
    # that blocking on it would strand orders. An uncoded delivery still
    # closes; its money just waits out a dispute window first.
    handover_code = models.CharField(max_length=6, blank=True, default='')
    delivery_confirmed_by_code = models.BooleanField(default=False)

    # Reasons
    cancellation_reason = models.TextField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)
    
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
    service_type = models.ForeignKey('laundries.Category', on_delete=models.SET_NULL, null=True, related_name='order_items')
    
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

class OrderStatusHistory(models.Model):
    """Audit log for order status transitions."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_history')
    
    previous_status = models.CharField(max_length=20, null=True, blank=True)
    new_status = models.CharField(max_length=20)
    
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='order_status_changes'
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = _('Order Status History')
        verbose_name_plural = _('Order Status Histories')
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['order', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.order.order_no}: {self.previous_status} -> {self.new_status}"
