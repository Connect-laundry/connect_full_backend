import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

class FAQ(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.CharField(max_length=255)
    answer = models.TextField()
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('FAQ')
        verbose_name_plural = _('FAQs')
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.question

class Feedback(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='feedbacks')
    
    subject = models.CharField(max_length=150)
    message = models.TextField()
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Feedback')
        verbose_name_plural = _('Feedbacks')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subject} - {self.user.email if self.user else 'Anonymous'}"

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
