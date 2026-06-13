# pyre-ignore[missing-module]
from rest_framework import permissions
# pyre-ignore[missing-module]
from users.models import User

class IsLaundryOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of a laundry to edit it.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to the owner of the laundry.
        return obj.owner == request.user

class IsOwnerRole(permissions.BasePermission):
    """Only authenticated users with the OWNER role may manage their laundry."""

    message = 'Only laundry owners can access this resource.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'role', None) == User.Role.OWNER
        )

