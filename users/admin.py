# pyre-ignore[missing-module]
from django.contrib import admin
# pyre-ignore[missing-module]
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
# pyre-ignore[missing-module]
from .models import DeviceSession, SessionRefreshToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ('email',)
    list_display = (
        'email', 
        'phone', 
        'first_name', 
        'last_name', 
        'role', 
        'is_verified', 
        'is_staff', 
        'is_active'
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
    
    add_fieldsets = (
        (None, {
            'classes': ('extrapretty',),
            'fields': ('email', 'phone', 'password', 'role', 'is_staff', 'is_superuser'),
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'platform',
        'device_id',
        'ip_address',
        'last_used_at',
        'revoked_at',
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


@admin.register(SessionRefreshToken)
class SessionRefreshTokenAdmin(admin.ModelAdmin):
    list_display = (
        'session',
        'jti',
        'issued_at',
        'expires_at',
        'revoked_at',
        'reuse_detected_at',
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
