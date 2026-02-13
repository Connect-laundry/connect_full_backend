from rest_framework import viewsets, permissions, status, decorators
from rest_framework.response import Response
from django.utils import timezone
from django.db import transaction
import logging

from ..models.laundry import Laundry
from ..models.service import Service
from ..serializers.laundry_detail import LaundryDetailSerializer # Existing or create a specialized one
from rest_framework import serializers

logger = logging.getLogger(__name__)

class AdminLaundryApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Laundry
        fields = ['id', 'name', 'status', 'is_active', 'approved_at', 'rejected_at']
        read_only_fields = ['id', 'name', 'approved_at', 'rejected_at']

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
            
        return Response({
            "status": "success",
            "message": f"Laundry {laundry.name} has been rejected.",
            "data": self.get_serializer(laundry).data
        })

class AdminServiceViewSet(viewsets.GenericViewSet):
    """
    Platform administration endpoints for vetting services.
    """
    queryset = Service.objects.all()
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    @decorators.action(detail=True, methods=['patch'])
    def approve(self, request, pk=None):
        """Approve a specific service."""
        service = self.get_object()
        
        service.is_approved = True
        service.save()
        
        logger.info(f"Service {service.id} approved by admin {request.user.email}")
        
        return Response({
            "status": "success",
            "message": f"Service {service.name} has been approved.",
            "data": {
                "id": service.id,
                "is_approved": service.is_approved
            }
        })
