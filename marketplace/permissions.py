"""Permissions for platform-admin (operations center) endpoints."""
# pyre-ignore[missing-module]
from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    """Allow Django staff/superusers OR users with the ADMIN application role.

    Admin-panel JS authenticates via the session (is_staff); programmatic admin
    clients authenticate via JWT and may carry role == 'ADMIN'.
    """

    message = 'Administrator access required.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        return bool(
            user.is_staff
            or user.is_superuser
            or getattr(user, 'role', None) == 'ADMIN'
        )
