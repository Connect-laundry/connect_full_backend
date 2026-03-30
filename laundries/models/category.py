import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _


class Category(models.Model):
    class CategoryType(models.TextChoices):
        SERVICE_TYPE = 'SERVICE_TYPE', _('Service Type')
        ITEM_CATEGORY = 'ITEM_CATEGORY', _('Item Category')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        _('name'),
        max_length=100,
        unique=True,
        db_index=True)
    type = models.CharField(
        _('type'),
        max_length=20,
        choices=CategoryType.choices,
        default=CategoryType.SERVICE_TYPE,
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Category')
        verbose_name_plural = _('Categories')
        ordering = ['name']

    def __str__(self):
        return self.name
