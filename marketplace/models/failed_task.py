import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _

class FailedTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_id = models.CharField(max_length=255, unique=True)
    task_name = models.CharField(max_length=255)
    args = models.JSONField(null=True, blank=True)
    kwargs = models.JSONField(null=True, blank=True)
    exception = models.TextField()
    stack_trace = models.TextField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    failed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Failed Task')
        verbose_name_plural = _('Failed Tasks')
        ordering = ['-failed_at']

    def __str__(self):
        return f"{self.task_name} ({self.task_id})"
