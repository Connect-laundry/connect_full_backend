from django.contrib import admin
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import ClerkWebhookEvent, DeviceSession, SessionRefreshToken, User
from django.utils.html import format_html
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    ordering = ('email',)
    list_display = (
        'profile_picture',
        'display_email',
        'phone',
        'display_role',
        'display_auth_provider',
        'short_clerk_id',
        'email_verified',
        'last_clerk_sign_in_at',
        'last_clerk_sync',
        'sync_health',
        'display_status',
        'is_staff',
    )
    list_filter = (
        'role',
        'auth_provider',
        'social_provider',
        'email_verified',
        'phone_verified',
        'clerk_status',
        'is_staff',
        'is_superuser',
        'is_active',
        'is_verified',
    )
    search_fields = ('email', 'primary_email', 'phone', 'first_name', 'last_name', 'clerk_user_id')
    actions = ['resync_clerk_users']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal Info'), {'fields': ('first_name', 'last_name', 'phone', 'role')}),
        (_('Clerk Identity'), {
            'fields': (
                'clerk_dashboard_link',
                'clerk_user_id',
                'auth_provider',
                'primary_email',
                'email_verified',
                'phone_verified',
                'social_provider',
                'social_profile_image_url',
                'last_social_login_at',
                'last_clerk_sign_in_at',
                'last_clerk_sync',
                'clerk_created_at',
                'clerk_updated_at',
                'clerk_status',
                'clerk_metadata',
            )
        }),
        (_('Permissions'), {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'is_verified', 'groups', 'user_permissions'),
        }),
        (_('Important dates'), {'fields': ('last_login', 'created_at', 'updated_at')}),
    )

    readonly_fields = (
        'clerk_dashboard_link',
        'clerk_user_id',
        'auth_provider',
        'primary_email',
        'email_verified',
        'phone_verified',
        'social_provider',
        'social_profile_image_url',
        'last_social_login_at',
        'last_clerk_sign_in_at',
        'last_clerk_sync',
        'clerk_created_at',
        'clerk_updated_at',
        'clerk_status',
        'clerk_metadata',
        'created_at',
        'updated_at',
    )

    def save_model(self, request, obj, form, change):
        """Audit role changes made through the admin panel.

        `role == ADMIN` confers broad API authority (manage any order, all
        logistics, dashboards), so escalations are logged at WARNING level with
        the acting admin, the target user, and the old -> new role. The log is
        emitted as a structured JSON record (see config.logging_formatters) and
        therefore reaches the console/log aggregation and Sentry.
        """
        previous_role = None
        if change and obj.pk:
            previous_role = (
                User.objects.filter(pk=obj.pk)
                .values_list('role', flat=True)
                .first()
            )

        super().save_model(request, obj, form, change)

        new_role = obj.role
        if previous_role == new_role:
            return

        actor = getattr(request, 'user', None)
        context = {
            'event': 'admin_user_role_changed',
            'actor_id': str(getattr(actor, 'id', '')) or None,
            'actor_email': getattr(actor, 'email', None),
            'target_user_id': str(obj.pk),
            'target_email': obj.email,
            'previous_role': previous_role,
            'new_role': new_role,
        }

        if new_role == User.Role.ADMIN:
            logger.warning(
                "User role escalated to ADMIN via admin panel", extra=context
            )
        else:
            logger.info("User role changed via admin panel", extra=context)

        # Persist a queryable audit record (best-effort).
        try:
            from marketplace.services.audit import record_audit
            from marketplace.models import AuditLog
            record_audit(
                action=AuditLog.Action.USER_ROLE_CHANGED,
                request=request,
                target_type='User',
                target_id=str(obj.pk),
                target_repr=obj.email,
                metadata={'previous_role': previous_role, 'new_role': new_role},
            )
        except Exception:  # pragma: no cover - defensive
            pass

    @display(description="Photo")
    def profile_picture(self, obj):
        image_url = obj.social_profile_image_url
        if not image_url:
            return "-"
        return format_html(
            '<img src="{}" alt="" style="width:32px;height:32px;border-radius:50%;object-fit:cover;" />',
            image_url,
        )

    @display(description="Provider", ordering="auth_provider")
    def display_auth_provider(self, obj):
        return obj.auth_provider or obj.social_provider or "local"

    @display(description="Clerk ID", ordering="clerk_user_id")
    def short_clerk_id(self, obj):
        if not obj.clerk_user_id:
            return "-"
        value = obj.clerk_user_id
        return value if len(value) <= 18 else f"{value[:10]}...{value[-6:]}"

    @display(description="Sync Health", label={
        "Healthy": "success",
        "Stale": "warning",
        "Unsynced": "warning",
        "Local": "info",
        "Deleted": "danger",
    })
    def sync_health(self, obj):
        if not obj.clerk_user_id:
            return "Local"
        if obj.clerk_status == 'deleted' or not obj.is_active:
            return "Deleted"
        if not obj.last_clerk_sync:
            return "Unsynced"
        if obj.last_clerk_sync < timezone.now() - timedelta(days=7):
            return "Stale"
        return "Healthy"

    @display(description="Open in Clerk")
    def clerk_dashboard_link(self, obj):
        if not obj.clerk_user_id:
            return "-"
        template = getattr(settings, 'CLERK_DASHBOARD_USER_URL_TEMPLATE', '')
        url = template.format(clerk_user_id=obj.clerk_user_id)
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">Open in Clerk Dashboard</a>', url)

    @admin.action(description="Force resync selected users from Clerk")
    def resync_clerk_users(self, request, queryset):
        from users.services.clerk_service import fetch_clerk_profile_by_user_id, sync_user_from_clerk

        synced = 0
        skipped = 0
        failed = 0
        for user in queryset:
            if not user.clerk_user_id:
                skipped += 1
                continue
            try:
                profile = fetch_clerk_profile_by_user_id(user.clerk_user_id)
                sync_user_from_clerk(profile=profile, request=request, source='admin_resync')
                synced += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    'Admin Clerk resync failed',
                    extra={'target_user_id': str(user.id), 'error_type': type(exc).__name__},
                )

        messages.info(request, f'Clerk resync complete. Synced: {synced}. Skipped: {skipped}. Failed: {failed}.')

    @display(description="Email", ordering="email")
    def display_email(self, obj):
        return format_html(
            '<div class="flex flex-col"><span>{}</span><span class="text-xs text-gray-500">{} {}</span></div>',
            obj.email, obj.first_name, obj.last_name
        )

    @display(description="Role", label={
        "CUSTOMER": "info",
        "OWNER": "success",
        "DRIVER": "warning",
        "ADMIN": "danger",
    })
    def display_role(self, obj):
        return obj.role

    @display(description="Status", label={
        "Verified": "success",
        "Active": "info",
        "Inactive": "danger",
    })
    def display_status(self, obj):
        if obj.is_active and obj.is_verified:
            return "Verified"
        elif obj.is_active:
            return "Active"
        return "Inactive"


@admin.register(DeviceSession)
class DeviceSessionAdmin(ModelAdmin):
    list_display = (
        'user',
        'platform_icon',
        'device_id',
        'ip_address',
        'last_used_at',
        'is_revoked',
    )
    list_filter = ('platform', 'revoked_at', 'created_at')
    search_fields = ('user__email', 'device_id', 'ip_address', 'user_agent')
    readonly_fields = (
        'id',
        'session_family_id',
        'current_refresh_jti',
        'current_refresh_expires_at',
        'created_at',
        'last_used_at',
        'revoked_at',
    )
    actions = ['revoke_sessions']

    @display(description="Platform")
    def platform_icon(self, obj):
        icons = {
            'ios': '📱 iOS',
            'android': '🤖 Android',
            'web': '🌐 Web'
        }
        return icons.get(obj.platform, obj.platform)

    @display(description="Revoked", boolean=True)
    def is_revoked(self, obj):
        return obj.revoked_at is not None

    @admin.action(description="Revoke selected sessions")
    def revoke_sessions(self, request, queryset):
        from django.utils import timezone
        queryset.update(revoked_at=timezone.now())


@admin.register(SessionRefreshToken)
class SessionRefreshTokenAdmin(ModelAdmin):
    list_display = (
        'session',
        'jti',
        'issued_at',
        'expires_at',
        'is_active',
    )
    list_filter = ('revoked_at', 'reuse_detected_at', 'issued_at')
    search_fields = ('jti', 'session__user__email', 'session__device_id')
    readonly_fields = (
        'id',
        'session',
        'jti',
        'issued_at',
        'expires_at',
        'rotated_at',
        'replaced_by_jti',
        'revoked_at',
        'revoked_reason',
        'reuse_detected_at',
    )

    @display(description="Active", boolean=True)
    def is_active(self, obj):
        from django.utils import timezone
        return obj.revoked_at is None and obj.expires_at > timezone.now()


@admin.register(ClerkWebhookEvent)
class ClerkWebhookEventAdmin(ModelAdmin):
    list_display = (
        'svix_id',
        'event_type',
        'clerk_user_id',
        'status',
        'received_at',
        'processed_at',
    )
    list_filter = ('event_type', 'status', 'received_at')
    search_fields = ('svix_id', 'clerk_user_id', 'event_type')
    readonly_fields = (
        'id',
        'svix_id',
        'event_type',
        'clerk_user_id',
        'status',
        'received_at',
        'processed_at',
        'error',
    )
