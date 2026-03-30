# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.utils import timezone

class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)

class SoftDeleteMixin(models.Model):
    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivation_reason = models.TextField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def deactivate(self, reason=None):
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.deactivation_reason = reason
        self.save()

    def activate(self):
        self.is_active = True
        self.deactivated_at = None
        self.deactivation_reason = None
        self.save()
