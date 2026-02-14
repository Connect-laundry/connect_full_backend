import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _

class LegalDocument(models.Model):
    class Type(models.TextChoices):
        TOS = 'TOS', _('Terms of Service')
        PRIVACY = 'PRIVACY', _('Privacy Policy')
        ABOUT = 'ABOUT', _('About Us')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        unique=True
    )
    title = models.CharField(_('title'), max_length=255)
    content = models.TextField(_('content'))
    version = models.CharField(_('version'), max_length=20, default='1.0')
    
    is_active = models.BooleanField(default=True)
    published_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Legal Document')
        verbose_name_plural = _('Legal Documents')

    def __str__(self):
        return self.get_document_type_display()
