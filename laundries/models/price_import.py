"""AI-assisted price-list import.

Owners upload a photo of a printed/handwritten price list. The backend sanitises
the image, asks an extraction provider (Gemini primary, OCR.space fallback /
cross-check — see ``laundries/services/price_import``) for candidate items, runs
deterministic validation over them and stores them as *drafts*
(``PriceListDraftItem``). Nothing becomes live pricing until the owner reviews
and explicitly confirms; confirmation is atomic and idempotent.

Every import requires review: ``READY`` means "extracted, owner review
required" (documented to the web app as REVIEW_REQUIRED).
"""
import uuid

# pyre-ignore[missing-module]
from django.conf import settings
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
        CANCELLED = 'CANCELLED', _('Cancelled')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Null while the owner is still onboarding (the laundry is created at the
    # wizard's last step); such scans belong to ``created_by`` and only
    # prefill the wizard. Attached to the laundry if later confirmed.
    laundry = models.ForeignKey(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='price_import_jobs',
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='price_import_jobs',
    )
    # Sanitised (re-encoded, metadata-stripped) copy; optional so a media
    # storage outage never blocks extraction. Purged after the retention window
    # by ``manage.py purge_price_import_images``.
    source_image = models.ImageField(
        _('source image'),
        upload_to='price_imports/',
        validators=[validate_file_upload],
        blank=True,
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    # Which provider produced the drafts ('gemini', 'ocr_space', 'none').
    provider = models.CharField(max_length=40, blank=True, default='')
    model_name = models.CharField(max_length=60, blank=True, default='')
    # Owner-safe failure message (never a raw provider error).
    error = models.CharField(max_length=255, blank=True, default='')
    error_code = models.CharField(max_length=40, blank=True, default='')

    # SHA-256 of the *normalised* image bytes; per-laundry dedup key.
    image_sha256 = models.CharField(max_length=64, blank=True, default='', db_index=True)
    original_filename = models.CharField(max_length=120, blank=True, default='')
    currency = models.CharField(max_length=3, blank=True, default='')
    document_warnings = models.JSONField(default=list, blank=True)
    # Sanitised structured audit trail: provider attempts, outcomes, latency,
    # token counts, cross-check summary. Never raw provider payloads or keys.
    provider_trace = models.JSONField(default=dict, blank=True)
    # Normalised extraction kept so an identical re-upload can be served
    # without another paid provider call.
    result = models.JSONField(default=dict, blank=True)
    # Stored confirm outcome; replayed for an idempotent repeat confirm.
    confirm_result = models.JSONField(default=dict, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    served_from_cache = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Price List Import Job')
        verbose_name_plural = _('Price List Import Jobs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['laundry', 'status']),
            models.Index(fields=['laundry', 'image_sha256']),
            models.Index(fields=['created_at']),
            models.Index(fields=['created_by', 'image_sha256']),
        ]

    def __str__(self):
        return f"ImportJob {self.id} ({self.status})"


class PriceListDraftItem(models.Model):
    """An unconfirmed candidate item extracted from an import job."""

    class PricingMethod(models.TextChoices):
        PER_ITEM = 'PER_ITEM', _('Per item')
        PER_KG = 'PER_KG', _('Per kg')
        UNKNOWN = 'UNKNOWN', _('Unknown')

    class ReviewState(models.TextChoices):
        # Owner-facing, deliberately not a percentage: provider confidence is
        # not calibrated.
        LOOKS_GOOD = 'LOOKS_GOOD', _('Looks good')
        CHECK = 'CHECK', _('Please check')
        UNREADABLE = 'UNREADABLE', _('Could not read')

    class MatchType(models.TextChoices):
        NONE = 'NONE', _('New')
        EXACT = 'EXACT', _('Same name exists')
        POSSIBLE = 'POSSIBLE', _('Possible match')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        PriceListImportJob,
        on_delete=models.CASCADE,
        related_name='draft_items',
    )
    # Proposed live name (normalised garment + variant). Kept under its
    # historical name for web-app compatibility.
    item_name = models.CharField(max_length=120)
    raw_name = models.CharField(max_length=200, blank=True, default='')
    variant = models.CharField(max_length=80, blank=True, default='')
    pricing_method = models.CharField(
        max_length=10, choices=PricingMethod.choices, default=PricingMethod.UNKNOWN
    )
    # Per-item price (PER_ITEM). Null when unreadable/absent.
    suggested_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    price_per_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    surcharge_type = models.CharField(max_length=40, blank=True, default='')
    surcharge_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    category = models.CharField(max_length=80, blank=True, default='')
    # Evidence from the image, as read.
    source_text = models.CharField(max_length=300, blank=True, default='')
    confidence = models.FloatField(null=True, blank=True)
    review_state = models.CharField(
        max_length=12, choices=ReviewState.choices, default=ReviewState.CHECK
    )
    warnings = models.JSONField(default=list, blank=True)
    match_type = models.CharField(
        max_length=10, choices=MatchType.choices, default=MatchType.NONE
    )
    matched_item = models.ForeignKey(
        'laundries.LaundryPricingItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    position = models.PositiveIntegerField(default=0)
    # Owners may deselect rows they don't want imported before confirming.
    is_selected = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Price List Draft Item')
        verbose_name_plural = _('Price List Draft Items')
        ordering = ['position', 'item_name']

    def __str__(self):
        return f"{self.item_name} ({self.suggested_price})"
