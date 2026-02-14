import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _

class SpecialOffer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(_('title'), max_length=255)
    description = models.TextField(_('description'), blank=True)
    image = models.ImageField(_('image'), upload_to='special_offers/')
    
    # Metadata
    is_active = models.BooleanField(default=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0, help_text=_("Order in carousel"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', '-created_at']
        verbose_name = _('Special Offer')
        verbose_name_plural = _('Special Offers')

    def __str__(self):
        return self.title
