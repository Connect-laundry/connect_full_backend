from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import DeviceSession, SessionRefreshToken, User
from django.utils.html import format_html


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    ordering = ('email',)
    list_display = (
        'display_email', 
        'phone', 
        'display_role', 
        'display_status',
        'is_staff', 
    )
    list_filter = ('role', 'is_staff', 'is_superuser', 'is_active', 'is_verified')
    search_fields = ('email', 'phone', 'first_name', 'last_name')
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (_('Personal Info'), {'fields': ('first_name', 'last_name', 'phone', 'role')}),
        (_('Permissions'), {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'is_verified', 'groups', 'user_permissions'),
        }),
        (_('Important dates'), {'fields': ('last_login', 'created_at', 'updated_at')}),
    )
    
    readonly_fields = ('created_at', 'updated_at')

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
