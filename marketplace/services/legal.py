# pyre-ignore[missing-module]
from django.db import IntegrityError, transaction
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.utils.text import slugify

from marketplace.models import AuditLog
from marketplace.models.legal import LegalPage, UserLegalAcceptance
from marketplace.services.audit import record_audit


LEGACY_LEGAL_SLUGS = {
    'TOS': 'terms-of-service',
    'TERMS': 'terms-of-service',
    'TERMS_OF_SERVICE': 'terms-of-service',
    'PRIVACY': 'privacy-policy',
    'PRIVACY_POLICY': 'privacy-policy',
    'ABOUT': 'about-us',
    'ABOUT_US': 'about-us',
}


def normalize_legal_slug(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    upper = raw.upper().replace('-', '_')
    return LEGACY_LEGAL_SLUGS.get(upper, slugify(raw))


def latest_published_legal_pages(*, language_code='en'):
    return LegalPage.objects.published().filter(language_code=language_code).order_by('sort_order', 'title')


def get_published_legal_page(slug, *, version=None, language_code='en'):
    slug = normalize_legal_slug(slug)
    qs = LegalPage.objects.filter(slug=slug, language_code=language_code, is_public=True)
    if version:
        return qs.get(version_number=version, is_published=True)
    return qs.published().order_by('-published_at', '-updated_at').first()


def publish_legal_page(page, *, request=None, actor=None):
    with transaction.atomic():
        page = LegalPage.objects.select_for_update().get(pk=page.pk)
        LegalPage.objects.select_for_update().filter(
            slug=page.slug,
            language_code=page.language_code,
            is_active=True,
            is_published=True,
        ).exclude(pk=page.pk).update(is_active=False)

        page.is_active = True
        page.is_published = True
        page.is_public = True
        page.published_at = page.published_at or timezone.now()
        page.effective_date = page.effective_date or timezone.now()
        if actor is not None:
            page.last_modified_by = actor
        page.full_clean()
        page.save()

    record_audit(
        action=AuditLog.Action.LEGAL_DOCUMENT_PUBLISHED,
        request=request,
        actor=actor,
        target_type='LegalPage',
        target_id=page.id,
        target_repr=f'{page.slug} v{page.version_number}',
        metadata={'slug': page.slug, 'version': page.version_number},
    )
    return page


def archive_legal_page(page, *, request=None, actor=None):
    page.is_active = False
    page.is_published = False
    if actor is not None:
        page.last_modified_by = actor
    page.save(update_fields=['is_active', 'is_published', 'last_modified_by', 'content_html', 'updated_at'])
    record_audit(
        action=AuditLog.Action.LEGAL_DOCUMENT_ARCHIVED,
        request=request,
        actor=actor,
        target_type='LegalPage',
        target_id=page.id,
        target_repr=f'{page.slug} v{page.version_number}',
        metadata={'slug': page.slug, 'version': page.version_number},
    )
    return page


def create_new_legal_version(source, *, changes=None, request=None, actor=None, publish=False, audit_action=None):
    clone = source.clone_as_new_version(actor=actor, changes=changes or {}, publish=False)
    if publish:
        clone = publish_legal_page(clone, request=request, actor=actor)
    record_audit(
        action=audit_action or AuditLog.Action.LEGAL_DOCUMENT_UPDATED,
        request=request,
        actor=actor,
        target_type='LegalPage',
        target_id=clone.id,
        target_repr=f'{clone.slug} v{clone.version_number}',
        metadata={
            'slug': clone.slug,
            'version': clone.version_number,
            'previous_version_id': str(source.id),
        },
    )
    return clone


def rollback_legal_page(source, *, request=None, actor=None):
    return create_new_legal_version(
        source,
        request=request,
        actor=actor,
        publish=True,
        audit_action=AuditLog.Action.LEGAL_DOCUMENT_ROLLED_BACK,
        changes={
            'version_number': _next_available_version(source.slug, source.language_code, source.version_number),
            'change_log': f'Rolled back from {source.slug} v{source.version_number}.',
        },
    )


def record_legal_acceptance(page, *, user, request=None, platform='', app_version=''):
    ip_address = None
    user_agent = ''
    if request is not None:
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        ip_address = xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')

    acceptance, _created = UserLegalAcceptance.objects.get_or_create(
        user=user,
        legal_page=page,
        accepted_version=page.version_number,
        defaults={
            'ip_address': ip_address or None,
            'user_agent': user_agent,
            'platform': platform or '',
            'app_version': app_version or '',
        },
    )
    record_audit(
        action=AuditLog.Action.LEGAL_ACCEPTANCE_RECORDED,
        request=request,
        actor=user,
        target_type='LegalPage',
        target_id=page.id,
        target_repr=f'{page.slug} v{page.version_number}',
        metadata={'slug': page.slug, 'version': page.version_number},
    )
    return acceptance


def _next_available_version(slug, language_code, base_version):
    candidate = LegalPage.next_minor_version(base_version)
    while LegalPage.objects.filter(slug=slug, language_code=language_code, version_number=candidate).exists():
        candidate = LegalPage.next_minor_version(candidate)
    return candidate


def save_imported_legal_page(*, title, document_type, content_markdown, slug='', version_number='1.0',
                             change_log='', actor=None, publish=True):
    slug = normalize_legal_slug(slug or title)
    document_type = LegalPage.normalize_document_type(document_type or title)
    existing = LegalPage.objects.filter(slug=slug, language_code='en').order_by('-created_at').first()
    if existing:
        version_number = _next_available_version(slug, 'en', existing.version_number)
        page = create_new_legal_version(
            existing,
            actor=actor,
            publish=publish,
            changes={
                'title': title,
                'document_type': document_type,
                'content_markdown': content_markdown,
                'version_number': version_number,
                'change_log': change_log or 'Imported from DOCX as a new version.',
            },
        )
        return page, False

    page = LegalPage(
        title=title,
        slug=slug,
        document_type=document_type,
        content_markdown=content_markdown,
        version_number=version_number,
        change_log=change_log or 'Imported from DOCX.',
        is_public=True,
        is_active=True,
        is_published=False,
        effective_date=timezone.now(),
        last_modified_by=actor,
    )
    page.full_clean()
    try:
        page.save()
    except IntegrityError:
        version_number = _next_available_version(slug, 'en', version_number)
        page.version_number = version_number
        page.save()

    if publish:
        page = publish_legal_page(page, actor=actor)
    record_audit(
        action=AuditLog.Action.LEGAL_DOCUMENT_CREATED,
        actor=actor,
        target_type='LegalPage',
        target_id=page.id,
        target_repr=f'{page.slug} v{page.version_number}',
        metadata={'slug': page.slug, 'version': page.version_number, 'imported': True},
    )
    return page, True
