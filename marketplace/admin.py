import difflib
import uuid

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import (
    FAQ,
    Feedback,
    FailedTask,
    PushDevice,
    Notification,
    AuditLog,
    LegalPage,
    UserLegalAcceptance,
)
from .services.audit import record_audit
from .services.legal import archive_legal_page, publish_legal_page, rollback_legal_page


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ('title', 'audience', 'category', 'priority', 'is_read', 'created_at')
    list_filter = ('audience', 'category', 'priority', 'is_read', 'created_at')
    search_fields = ('title', 'body', 'user__email', 'category')
    readonly_fields = ('id', 'created_at', 'read_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
    list_display = ('created_at', 'action', 'actor_email', 'target_type', 'target_repr')
    list_filter = ('action', 'target_type', 'created_at')
    search_fields = ('actor_email', 'target_repr', 'target_id')
    readonly_fields = (
        'id', 'actor', 'actor_email', 'action', 'target_type', 'target_id',
        'target_repr', 'metadata', 'ip_address', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('actor')


@admin.register(LegalPage)
class LegalPageAdmin(ModelAdmin):
    list_display = (
        'title', 'slug', 'document_type', 'version_number', 'display_status',
        'requires_user_reacceptance', 'language_code', 'published_at', 'updated_at',
    )
    list_filter = (
        'document_type', 'is_published', 'is_active', 'is_public',
        'requires_user_reacceptance', 'language_code', 'version_number',
    )
    search_fields = ('title', 'slug', 'document_type', 'summary', 'content_markdown')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = (
        'id', 'content_html', 'preview_pane', 'version_history', 'previous_version',
        'created_at', 'updated_at', 'published_at',
    )
    actions = (
        'publish_selected', 'unpublish_selected', 'archive_selected',
        'duplicate_as_new_version', 'rollback_selected', 'compare_selected_versions',
    )
    fieldsets = (
        ('Document', {
            'fields': (
                'title', 'slug', 'document_type', 'short_description',
                'summary', 'content_markdown', 'preview_pane',
            ),
        }),
        ('Publishing', {
            'fields': (
                'version_number', 'effective_date', 'published_at',
                'requires_user_reacceptance', 'is_active', 'is_published',
                'is_public', 'language_code', 'sort_order',
            ),
        }),
        ('SEO & Metadata', {
            'fields': ('seo_title', 'seo_description', 'tags', 'change_log'),
            'classes': ('collapse',),
        }),
        ('Version History', {
            'fields': ('version_history', 'previous_version', 'last_modified_by', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
        ('Generated HTML', {
            'fields': ('content_html',),
            'classes': ('collapse',),
        }),
    )

    class Media:
        css = {'all': ('admin/legal_cms.css',)}
        js = ('admin/legal_cms.js',)

    @display(description='Status', label={
        'published': 'success',
        'draft': 'warning',
        'archived': 'danger',
    })
    def display_status(self, obj):
        if not obj.is_active:
            return 'archived'
        return 'published' if obj.is_published else 'draft'

    @display(description='Preview')
    def preview_pane(self, obj):
        if not obj or not obj.content_html:
            return format_html('<div class="legal-preview empty">Save to generate preview.</div>')
        return format_html('<article class="legal-preview">{}</article>', mark_safe(obj.content_html))

    @display(description='Version history')
    def version_history(self, obj):
        if not obj or not obj.slug:
            return 'Save this document to start version history.'
        versions = LegalPage.objects.filter(
            slug=obj.slug,
            language_code=obj.language_code,
        ).order_by('-created_at')[:12]
        rows = [
            f'<li>v{page.version_number} - {"published" if page.is_published else "draft"}'
            f'{" - current" if page.is_active and page.is_published else ""}</li>'
            for page in versions
        ]
        return mark_safe('<ul>' + ''.join(rows) + '</ul>')

    def save_model(self, request, obj, form, change):
        obj.last_modified_by = request.user
        if change:
            original = LegalPage.objects.get(pk=obj.pk)
            if original.is_published and form.changed_data:
                obj.pk = None
                obj.id = uuid.uuid4()
                obj.previous_version = original
                obj.version_number = (
                    obj.version_number
                    if 'version_number' in form.changed_data
                    else LegalPage.next_minor_version(original.version_number)
                )
                obj.is_published = False
                obj.published_at = None
                super().save_model(request, obj, form, False)
                self.message_user(
                    request,
                    'Published legal documents are immutable. A new draft version was created.',
                    messages.INFO,
                )
                record_audit(
                    action=AuditLog.Action.LEGAL_DOCUMENT_UPDATED,
                    request=request,
                    target_type='LegalPage',
                    target_id=obj.id,
                    target_repr=f'{obj.slug} v{obj.version_number}',
                    metadata={'previous_version_id': str(original.id)},
                )
                return
        super().save_model(request, obj, form, change)
        record_audit(
            action=AuditLog.Action.LEGAL_DOCUMENT_UPDATED if change else AuditLog.Action.LEGAL_DOCUMENT_CREATED,
            request=request,
            target_type='LegalPage',
            target_id=obj.id,
            target_repr=f'{obj.slug} v{obj.version_number}',
        )

    @admin.action(description='Publish selected legal pages')
    def publish_selected(self, request, queryset):
        count = 0
        for page in queryset:
            publish_legal_page(page, request=request, actor=request.user)
            count += 1
        self.message_user(request, f'{count} legal page(s) published.', messages.SUCCESS)

    @admin.action(description='Archive selected legal pages')
    def archive_selected(self, request, queryset):
        count = 0
        for page in queryset:
            archive_legal_page(page, request=request, actor=request.user)
            count += 1
        self.message_user(request, f'{count} legal page(s) archived.', messages.SUCCESS)

    @admin.action(description='Unpublish selected legal pages')
    def unpublish_selected(self, request, queryset):
        count = queryset.update(is_published=False)
        for page in queryset:
            record_audit(
                action=AuditLog.Action.LEGAL_DOCUMENT_ARCHIVED,
                request=request,
                target_type='LegalPage',
                target_id=page.id,
                target_repr=f'{page.slug} v{page.version_number}',
                metadata={'unpublished': True},
            )
        self.message_user(request, f'{count} legal page(s) unpublished.', messages.SUCCESS)

    @admin.action(description='Duplicate selected as new draft version')
    def duplicate_as_new_version(self, request, queryset):
        count = 0
        for page in queryset:
            page.clone_as_new_version(actor=request.user)
            count += 1
        self.message_user(request, f'{count} draft version(s) created.', messages.SUCCESS)

    @admin.action(description='Rollback selected version and publish it as current')
    def rollback_selected(self, request, queryset):
        count = 0
        for page in queryset:
            rollback_legal_page(page, request=request, actor=request.user)
            count += 1
        self.message_user(request, f'{count} rollback version(s) published.', messages.SUCCESS)

    @admin.action(description='Compare exactly two selected versions')
    def compare_selected_versions(self, request, queryset):
        pages = list(queryset.order_by('slug', 'version_number'))
        if len(pages) != 2:
            self.message_user(request, 'Select exactly two versions to compare.', messages.ERROR)
            return
        before, after = pages
        diff = difflib.unified_diff(
            before.content_markdown.splitlines(),
            after.content_markdown.splitlines(),
            fromfile=f'{before.slug} v{before.version_number}',
            tofile=f'{after.slug} v{after.version_number}',
            lineterm='',
        )
        preview = '\n'.join(list(diff)[:30]) or 'No content differences found.'
        self.message_user(request, preview[:1800], messages.INFO)


@admin.register(UserLegalAcceptance)
class UserLegalAcceptanceAdmin(ModelAdmin):
    list_display = ('user', 'legal_page', 'accepted_version', 'platform', 'app_version', 'accepted_at')
    list_filter = ('platform', 'app_version', 'accepted_at')
    search_fields = ('user__email', 'legal_page__slug', 'legal_page__title', 'accepted_version')
    readonly_fields = (
        'id', 'user', 'legal_page', 'accepted_version', 'accepted_at',
        'ip_address', 'user_agent', 'platform', 'app_version',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FAQ)
class FAQAdmin(ModelAdmin):
    list_display = ('question', 'display_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('question', 'answer')

    @display(description="Active", boolean=True)
    def display_active(self, obj):
        return obj.is_active


@admin.register(Feedback)
class FeedbackAdmin(ModelAdmin):
    list_display = ('user', 'subject', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('subject', 'message', 'user__email')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


@admin.register(FailedTask)
class FailedTaskAdmin(ModelAdmin):
    list_display = ('task_name', 'task_id', 'failed_at', 'retry_count')
    list_filter = ('failed_at', 'task_name')
    search_fields = ('task_name', 'task_id', 'exception')
    readonly_fields = ('task_id', 'task_name', 'exception', 'retry_count', 'failed_at')


@admin.register(PushDevice)
class PushDeviceAdmin(ModelAdmin):
    list_display = ('user', 'platform', 'is_active', 'last_registered_at')
    list_filter = ('platform', 'is_active')
    search_fields = ('user__email', 'device_id', 'token')
    readonly_fields = ('token', 'device_id', 'platform', 'app_version', 'last_registered_at', 'created_at')
