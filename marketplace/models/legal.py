# pyre-ignore[missing-module]
import re
import uuid

# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.core.exceptions import ValidationError
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.db.models import Q
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.utils.text import slugify
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

from marketplace.utils.legal_content import render_markdown_to_safe_html


class LegalPageQuerySet(models.QuerySet):
    def published(self):
        now = timezone.now()
        return self.filter(
            is_active=True,
            is_published=True,
            is_public=True,
        ).filter(Q(effective_date__isnull=True) | Q(effective_date__lte=now))

    def for_slug(self, slug, language_code='en'):
        return self.filter(slug=slug, language_code=language_code)


class LegalPage(models.Model):
    """Versioned legal CMS document.

    Each row is one immutable-ish document version. Publishing a version makes it
    the current active public record for its slug/language and keeps older rows
    available for audit, rollback, and version-specific retrieval.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(_('title'), max_length=255)
    slug = models.SlugField(max_length=140, db_index=True)
    document_type = models.CharField(max_length=80, db_index=True)
    short_description = models.CharField(max_length=300, blank=True, default='')
    content_markdown = models.TextField(_('content markdown'))
    content_html = models.TextField(_('content html'), blank=True, default='', editable=False)
    summary = models.TextField(blank=True, default='')
    version_number = models.CharField(_('version'), max_length=20, default='1.0')
    effective_date = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='legal_pages_modified',
    )
    requires_user_reacceptance = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_published = models.BooleanField(default=False)
    is_public = models.BooleanField(default=True)
    seo_title = models.CharField(max_length=255, blank=True, default='')
    seo_description = models.CharField(max_length=300, blank=True, default='')
    previous_version = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='next_versions',
    )
    change_log = models.TextField(blank=True, default='')
    tags = models.JSONField(default=list, blank=True)
    language_code = models.CharField(max_length=10, default='en', db_index=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LegalPageQuerySet.as_manager()

    class Meta:
        verbose_name = _('Legal Page')
        verbose_name_plural = _('Legal Pages')
        ordering = ['sort_order', 'title', '-published_at', '-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['slug', 'version_number', 'language_code'],
                name='unique_legal_page_slug_version_language',
            ),
        ]
        indexes = [
            models.Index(fields=['slug', 'language_code', 'is_active', 'is_published'], name='marketplace_slug_18fb7f_idx'),
            models.Index(fields=['document_type', 'is_published', 'is_active'], name='marketplace_documen_2a4752_idx'),
            models.Index(fields=['published_at'], name='marketplace_publish_c79b73_idx'),
            models.Index(fields=['effective_date'], name='marketplace_effecti_34e1b2_idx'),
        ]

    def __str__(self):
        status = 'published' if self.is_published else 'draft'
        return f'{self.title} v{self.version_number} ({status})'

    @property
    def public_path(self):
        return f'/legal/{self.slug}/'

    @staticmethod
    def normalize_document_type(value):
        value = (value or '').strip()
        if not value:
            return ''
        return re.sub(r'[^A-Z0-9_]+', '_', value.upper()).strip('_')

    @staticmethod
    def next_minor_version(version):
        try:
            major, minor = str(version).split('.', 1)
            return f'{int(major)}.{int(minor) + 1}'
        except (TypeError, ValueError):
            return '1.1'

    def clean(self):
        super().clean()
        if not self.title:
            raise ValidationError({'title': _('Title is required.')})
        if not self.document_type:
            raise ValidationError({'document_type': _('Document type is required.')})
        if self.tags is None:
            self.tags = []
        if not isinstance(self.tags, list):
            raise ValidationError({'tags': _('Tags must be a list of strings.')})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        self.slug = slugify(self.slug)
        self.document_type = self.normalize_document_type(self.document_type or self.title)
        self.content_html = render_markdown_to_safe_html(self.content_markdown or '')
        super().save(*args, **kwargs)

    def clone_as_new_version(self, *, actor=None, changes=None, publish=False):
        changes = changes or {}
        clone = LegalPage(
            title=changes.get('title', self.title),
            slug=changes.get('slug', self.slug),
            document_type=changes.get('document_type', self.document_type),
            short_description=changes.get('short_description', self.short_description),
            content_markdown=changes.get('content_markdown', self.content_markdown),
            summary=changes.get('summary', self.summary),
            version_number=changes.get(
                'version_number',
                self.next_minor_version(self.version_number),
            ),
            effective_date=changes.get('effective_date', self.effective_date),
            last_modified_by=actor or changes.get('last_modified_by') or self.last_modified_by,
            requires_user_reacceptance=changes.get(
                'requires_user_reacceptance',
                self.requires_user_reacceptance,
            ),
            is_active=changes.get('is_active', True),
            is_published=False,
            is_public=changes.get('is_public', self.is_public),
            seo_title=changes.get('seo_title', self.seo_title),
            seo_description=changes.get('seo_description', self.seo_description),
            previous_version=self,
            change_log=changes.get('change_log', ''),
            tags=changes.get('tags', list(self.tags or [])),
            language_code=changes.get('language_code', self.language_code),
            sort_order=changes.get('sort_order', self.sort_order),
        )
        if publish:
            clone.published_at = timezone.now()
            clone.is_published = True
        clone.full_clean()
        clone.save()
        return clone


class LegalDocument(LegalPage):
    """Backward-compatible proxy for old imports and admin URLs."""

    class Meta:
        proxy = True
        verbose_name = _('Legal Document')
        verbose_name_plural = _('Legal Documents')


class UserLegalAcceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='legal_acceptances',
    )
    legal_page = models.ForeignKey(
        LegalPage,
        on_delete=models.CASCADE,
        related_name='acceptances',
    )
    accepted_version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    platform = models.CharField(max_length=40, blank=True, default='')
    app_version = models.CharField(max_length=40, blank=True, default='')

    class Meta:
        verbose_name = _('User Legal Acceptance')
        verbose_name_plural = _('User Legal Acceptances')
        ordering = ['-accepted_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'legal_page', 'accepted_version'],
                name='unique_user_legal_acceptance_version',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'accepted_at'], name='marketplace_user_id_5b9ed6_idx'),
            models.Index(fields=['legal_page', 'accepted_version'], name='marketplace_legal_p_f12a51_idx'),
        ]

    def __str__(self):
        return f'{self.user} accepted {self.legal_page.slug} v{self.accepted_version}'
