import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
# pyre-ignore[missing-module]
from django.core.validators import MinValueValidator, MaxValueValidator
# pyre-ignore[missing-module]
from ..utils.validators import validate_file_upload

class Laundry(models.Model):
    class PriceRange(models.TextChoices):
        LOW = '$', _('Low')
        MEDIUM = '$$', _('Medium')
        HIGH = '$$$', _('High')

    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        SUSPENDED = "SUSPENDED", _("Suspended")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_('name'), max_length=255, db_index=True)
    description = models.TextField(_('description'), blank=True)
    image = models.ImageField(
        _('image'), 
        upload_to='laundries/', 
        blank=True, 
        null=True,
        validators=[validate_file_upload]
    )
    address = models.TextField(_('address'))
    latitude = models.DecimalField(_('latitude'), max_digits=9, decimal_places=6, db_index=True)
    longitude = models.DecimalField(_('longitude'), max_digits=9, decimal_places=6, db_index=True)
    phone_number = models.CharField(_('phone number'), max_length=20)
    price_range = models.CharField(_('price range'), max_length=3, choices=PriceRange.choices, default=PriceRange.MEDIUM)
    estimated_delivery_hours = models.IntegerField(_('estimated delivery hours'), default=24)
    delivery_fee = models.DecimalField(_('delivery fee'), max_digits=10, decimal_places=2, default=0.00)
    
    is_featured = models.BooleanField(_('is featured'), default=False, db_index=True)
    is_active = models.BooleanField(_('is active'), default=False, db_index=True)
    
    status = models.CharField(
        _('approval status'),
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True
    )
    
    # Approval Timestamps
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    # Deactivation (Soft-Delete)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivation_reason = models.TextField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_laundries',
        limit_choices_to={'role': 'OWNER'}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Laundry')
        verbose_name_plural = _('Laundries')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['is_featured', 'is_active']),
            models.Index(fields=['price_range']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name
