# pyre-ignore[missing-module]
from rest_framework import permissions
# pyre-ignore[missing-module]
from .models.base import Order

class IsOrderParticipant(permissions.BasePermission):
    """
    Allows access to Orders only if the user is the customer or the laundry owner.
    """
    def has_object_permission(self, request, view, obj):
        return (
            obj.user == request.user or 
            obj.laundry.owner == request.user or
            request.user.is_staff or
            request.user.role == 'ADMIN'
        )

class CanManageLifecycle(permissions.BasePermission):
    """
    Enforces role-based rules for order lifecycle transitions.
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        
        # Admin can do anything
        if user.is_staff or user.role == 'ADMIN':
            return True
            
        # Customer can only cancel
        if user.role == 'CUSTOMER':
            return view.action == 'cancel'
            
        # Laundry Owner can manage most stages
        if user.role == 'OWNER' and obj.laundry.owner == user:
            return True
            
        # Driver can mark pickup and delivery milestones
        if user.role == 'DRIVER':
            return view.action in ['mark_picked_up', 'mark_delivered']
            
        return False
