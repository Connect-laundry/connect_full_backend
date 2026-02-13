import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _

class Payment(models.Model):
    """Core payment record for order transactions."""
    class Method(models.TextChoices):
        CARD = 'CARD', _('Card')
        TRANS = 'BANK_TRANSFER', _('Bank Transfer')
        CASH = 'CASH', _('Cash on Delivery')
        WALLET = 'WALLET', _('Wallet Balance')

    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        SUCCESS = 'SUCCESS', _('Successful')
        FAILED = 'FAILED', _('Failed')
        REFUNDED = 'REFUNDED', _('Refunded')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField('ordering.Order', on_delete=models.CASCADE, related_name='payment')
    
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='NGN')
    
    payment_method = models.CharField(max_length=20, choices=Method.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    
    transaction_reference = models.CharField(max_length=100, unique=True)
    gateway_response = models.JSONField(null=True, blank=True)
    
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment for {self.order.order_no} ({self.status})"
