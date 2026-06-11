# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status, decorators
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.db import transaction
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

    @decorators.action(detail=True, methods=['patch'])
    def approve(self, request, pk=None):
        """Approve a laundry business and make it active."""
        laundry = self.get_object()
        
        with transaction.atomic():
            laundry.status = Laundry.ApprovalStatus.APPROVED
            laundry.is_active = True
            laundry.approved_at = timezone.now()
            laundry.save()
            
            logger.info(f"Laundry {laundry.id} approved by admin {request.user.email}")

        self._audit_and_notify(request, laundry, approved=True)

        return Response({
            "status": "success",
            "message": f"Laundry {laundry.name} has been approved and is now active.",
            "data": self.get_serializer(laundry).data
        })

    @decorators.action(detail=True, methods=['patch'])
    def reject(self, request, pk=None):
        """Reject a laundry business."""
        laundry = self.get_object()
        reason = request.data.get('reason', 'No specific reason provided.')

        with transaction.atomic():
            laundry.status = Laundry.ApprovalStatus.REJECTED
            laundry.is_active = False
            laundry.rejected_at = timezone.now()
            laundry.save()

            logger.info(f"Laundry {laundry.id} rejected by admin {request.user.email}. Reason: {reason}")

        self._audit_and_notify(request, laundry, approved=False, reason=reason)

        return Response({
            "status": "success",
            "message": f"Laundry {laundry.name} has been rejected.",
            "data": self.get_serializer(laundry).data
        })

    @staticmethod
    def _audit_and_notify(request, laundry, *, approved, reason=''):
        """Write an audit record and push an owner notification. Best-effort."""
        try:
            from marketplace.services.audit import record_audit
            from marketplace.services.notification_service import NotificationService
            from marketplace.models import AuditLog, Notification
            action = (AuditLog.Action.LAUNDRY_APPROVED if approved
                      else AuditLog.Action.LAUNDRY_REJECTED)
            record_audit(
                action=action,
                request=request,
                target_type='Laundry',
                target_id=str(laundry.id),
                target_repr=laundry.name,
                metadata={} if approved else {'reason': reason},
            )
            owner = getattr(laundry, 'owner', None)
            if owner:
                if approved:
                    NotificationService.notify_user(
                        owner,
                        title="Laundry approved",
                        body=f"Your laundry '{laundry.name}' has been approved and is now live.",
                        category='LAUNDRY_APPROVED',
                        priority=Notification.Priority.HIGH,
                        dedup_key=f'laundry_approved:{laundry.id}',
                    )
                else:
                    NotificationService.notify_user(
                        owner,
                        title="Laundry rejected",
                        body=f"Your laundry '{laundry.name}' was not approved. Reason: {reason}",
                        category='LAUNDRY_REJECTED',
                        priority=Notification.Priority.HIGH,
                        dedup_key=f'laundry_rejected:{laundry.id}',
                    )
        except Exception:  # pragma: no cover - never break the admin action
            pass

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
