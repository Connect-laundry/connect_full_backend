"""AI-assisted price-list import.

Owners can upload a photo of a printed/handwritten price list. The backend
creates a draft extraction *job*; a pluggable OCR/vision provider proposes
candidate items (``PriceListDraftItem``). Nothing becomes live pricing until the
owner explicitly confirms — confirmation creates ``LaundryPricingItem`` rows and
never overwrites existing ones.

The provider layer lives in ``laundries/services/ocr.py`` and ships with a
null/stub provider so the workflow is fully exercisable before a real OCR
integration is wired in.
"""
import uuid

# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

from ..utils.validators import validate_file_upload


class PriceListImportJob(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        PROCESSING = 'PROCESSING', _('Processing')
        READY = 'READY', _('Ready for review')
        CONFIRMED = 'CONFIRMED', _('Confirmed')
        FAILED = 'FAILED', _('Failed')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='price_import_jobs',
    )
    source_image = models.ImageField(
        _('source image'),
        upload_to='price_imports/',
        validators=[validate_file_upload],
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    provider = models.CharField(max_length=40, blank=True, default='')
    error = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Price List Import Job')
        verbose_name_plural = _('Price List Import Jobs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['laundry', 'status']),
        ]

    def __str__(self):
        return f"ImportJob {self.id} ({self.status})"


class PriceListDraftItem(models.Model):
    """An unconfirmed candidate item extracted from an import job."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        PriceListImportJob,
        on_delete=models.CASCADE,
        related_name='draft_items',
    )
    item_name = models.CharField(max_length=120)
    suggested_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    category = models.CharField(max_length=80, blank=True, default='')
    confidence = models.FloatField(null=True, blank=True)
    # Owners may deselect rows they don't want imported before confirming.
    is_selected = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Price List Draft Item')
        verbose_name_plural = _('Price List Draft Items')
        ordering = ['item_name']

    def __str__(self):
        return f"{self.item_name} ({self.suggested_price})"
