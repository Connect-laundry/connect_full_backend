# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status, decorators
# pyre-ignore[missing-module]
from rest_framework.response import Response
import logging

# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.service import LaundryService
# pyre-ignore[missing-module]
from ..serializers.laundry_detail import LaundryDetailSerializer # Existing or create a specialized one
# pyre-ignore[missing-module]
from rest_framework import serializers

logger = logging.getLogger(__name__)

class AdminLaundryApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Laundry
        fields = ['id', 'name', 'status', 'is_active', 'approved_at', 'rejected_at']
        read_only_fields = ['id', 'name', 'approved_at', 'rejected_at']


class AdminServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = LaundryService
        fields = [
            'id', 'laundry', 'item', 'service_type', 'price',
            'estimated_duration', 'is_available', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AdminLaundryViewSet(viewsets.GenericViewSet):
    """
    Platform administration endpoints for vetting laundry businesses.
    """
    queryset = Laundry.objects.all()
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
    serializer_class = AdminLaundryApprovalSerializer

    def _decide(self, request, service_method, success_message, *, with_reason=False):
        """All decisions run through LaundryApprovalService — the same engine
        the Django admin buttons use — so audit/notifications/analytics are
        identical regardless of entry point."""
        from ..services.approval import InvalidTransition
        laundry = self.get_object()
        kwargs = {'actor': request.user, 'request': request}
        if with_reason:
            kwargs['reason'] = request.data.get('reason', '') or ''
        try:
            laundry = service_method(laundry, **kwargs)
        except InvalidTransition as exc:
            return Response(
                {"status": "error", "message": str(exc), "data": None},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({
            "status": "success",
            "message": success_message.format(name=laundry.name),
            "data": self.get_serializer(laundry).data,
        })

    @decorators.action(detail=True, methods=['patch'])
    def approve(self, request, pk=None):
        """Approve a laundry business and make it active."""
        from ..services.approval import LaundryApprovalService
        return self._decide(
            request, LaundryApprovalService.approve,
            "Laundry {name} has been approved and is now active.",
        )

    @decorators.action(detail=True, methods=['patch'])
    def reject(self, request, pk=None):
        """Reject a laundry business."""
        from ..services.approval import LaundryApprovalService
        return self._decide(
            request, LaundryApprovalService.reject,
            "Laundry {name} has been rejected.",
            with_reason=True,
        )

    @decorators.action(detail=True, methods=['patch'], url_path='request-changes')
    def request_changes(self, request, pk=None):
        """Ask the owner to fix specific issues before approval."""
        from ..services.approval import LaundryApprovalService
        return self._decide(
            request, LaundryApprovalService.request_changes,
            "Changes were requested on laundry {name}.",
            with_reason=True,
        )

    @decorators.action(detail=True, methods=['patch'])
    def suspend(self, request, pk=None):
        """Suspend an approved laundry."""
        from ..services.approval import LaundryApprovalService
        return self._decide(
            request, LaundryApprovalService.suspend,
            "Laundry {name} has been suspended.",
            with_reason=True,
        )

class AdminServiceViewSet(viewsets.GenericViewSet):
    """
    Platform administration endpoints for vetting services.
    """
    queryset = LaundryService.objects.all()
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
    serializer_class = AdminServiceSerializer

    @decorators.action(detail=True, methods=['patch'])
    def approve(self, request, pk=None):
        """Approve a specific service."""
        service = self.get_object()
        
        service.save()
        
        logger.info(f"LaundryService {service.id} processed by admin {request.user.email}")
        
        return Response({
            "status": "success",
            "message": f"Service handled.",
        })
