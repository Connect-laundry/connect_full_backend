import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

class Service(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='services'
    )
    category = models.ForeignKey(
        'laundries.Category',
        on_delete=models.SET_NULL,
        null=True,
        related_name='services'
    )
    name = models.CharField(_('name'), max_length=255)
    description = models.TextField(_('description'), blank=True)
    base_price = models.DecimalField(_('base price'), max_digits=10, decimal_places=2)
    is_active = models.BooleanField(_('is active'), default=True)
    is_approved = models.BooleanField(_('is approved'), default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Service')
        verbose_name_plural = _('Services')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} - {self.laundry.name}"
