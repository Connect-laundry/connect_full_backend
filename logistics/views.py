# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions
# pyre-ignore[missing-module]
from rest_framework.exceptions import PermissionDenied, ValidationError
# pyre-ignore[missing-module]
from ordering.models import Order
# pyre-ignore[missing-module]
from .models import DeliveryAssignment, TrackingLog
# pyre-ignore[missing-module]
from .serializers import DeliveryAssignmentSerializer, TrackingLogSerializer


def _visible_orders_for_user(user):
    if not user.is_authenticated:
        return Order.objects.none()

    if user.is_staff or getattr(user, 'role', None) == 'ADMIN':
        return Order.objects.all()

    if getattr(user, 'role', None) == 'DRIVER':
        return Order.objects.filter(delivery_assignments__driver=user)

    if getattr(user, 'role', None) == 'OWNER':
        return Order.objects.filter(laundry__owner=user)

    return Order.objects.filter(user=user)

class DeliveryAssignmentViewSet(viewsets.ModelViewSet):
    """Management of delivery assignments (Admin/Owner only usually)."""
    queryset = DeliveryAssignment.objects.all()
    serializer_class = DeliveryAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return self.queryset.none()

        if user.is_staff or getattr(user, 'role', None) == 'ADMIN':
            return self.queryset.select_related('order', 'driver').distinct()

        if getattr(user, 'role', None) == 'DRIVER':
            return self.queryset.filter(driver=user).select_related('order', 'driver').distinct()

        if getattr(user, 'role', None) == 'OWNER':
            return self.queryset.filter(order__laundry__owner=user).select_related('order', 'driver').distinct()

        return self.queryset.none()

    def _ensure_manage_permission(self):
        user = self.request.user
        role = getattr(user, 'role', None)
        if user.is_staff or role == 'ADMIN':
            return
        if role != 'OWNER':
            raise PermissionDenied('Only admins or laundry owners can manage delivery assignments.')

    def perform_create(self, serializer):
        self._ensure_manage_permission()
        order = serializer.validated_data['order']
        driver = serializer.validated_data['driver']
        user = self.request.user

        if getattr(driver, 'role', None) != 'DRIVER':
            raise ValidationError({'driver': 'Assignments can only be created for driver accounts.'})

        if getattr(user, 'role', None) == 'OWNER' and order.laundry.owner_id != user.id:
            raise PermissionDenied('You can only manage assignments for your own laundry orders.')

        serializer.save()

    def perform_update(self, serializer):
        self._ensure_manage_permission()
        instance = self.get_object()
        user = self.request.user
        if getattr(user, 'role', None) == 'OWNER' and instance.order.laundry.owner_id != user.id:
            raise PermissionDenied('You can only manage assignments for your own laundry orders.')

        driver = serializer.validated_data.get('driver')
        if driver is not None and getattr(driver, 'role', None) != 'DRIVER':
            raise ValidationError({'driver': 'Assignments can only be assigned to driver accounts.'})

        serializer.save()

    def perform_destroy(self, instance):
        self._ensure_manage_permission()
        user = self.request.user
        if getattr(user, 'role', None) == 'OWNER' and instance.order.laundry.owner_id != user.id:
            raise PermissionDenied('You can only manage assignments for your own laundry orders.')
        instance.delete()

class TrackingViewSet(viewsets.ReadOnlyModelViewSet):
    """Public/User tracking info for orders."""
    queryset = TrackingLog.objects.all()
    serializer_class = TrackingLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        order_id = self.request.query_params.get('order_id')
        visible_orders = _visible_orders_for_user(self.request.user)
        if order_id:
            return self.queryset.filter(order_id=order_id, order__in=visible_orders).select_related('order').distinct()
        return self.queryset.filter(order__in=visible_orders).select_related('order').distinct()
