import uuid
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

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
