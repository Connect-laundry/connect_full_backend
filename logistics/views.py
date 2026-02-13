# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions
# pyre-ignore[missing-module]
from .models import DeliveryAssignment, TrackingLog
# pyre-ignore[missing-module]
from .serializers import DeliveryAssignmentSerializer, TrackingLogSerializer

class DeliveryAssignmentViewSet(viewsets.ModelViewSet):
    """Management of delivery assignments (Admin/Owner only usually)."""
    queryset = DeliveryAssignment.objects.all()
    serializer_class = DeliveryAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Drivers see their assignments, Owners/Admins see all
        user = self.request.user
        if user.role == 'DRIVER':
            return self.queryset.filter(driver=user)
        return self.queryset

class TrackingViewSet(viewsets.ReadOnlyModelViewSet):
    """Public/User tracking info for orders."""
    queryset = TrackingLog.objects.all()
    serializer_class = TrackingLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        order_id = self.request.query_params.get('order_id')
        if order_id:
            return self.queryset.filter(order_id=order_id)
        return self.queryset.filter(order__user=self.request.user)
