# pyre-ignore[missing-module]
from django.contrib import admin
# pyre-ignore[missing-module]
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _
# pyre-ignore[missing-module]
from .models import User


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
