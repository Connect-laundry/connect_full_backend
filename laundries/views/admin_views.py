from rest_framework import (
    viewsets,
    permissions,
    status,
    decorators,
    mixins,
    serializers,
)
from django.core.exceptions import ValidationError as DjangoValidationError

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
# Existing or create a specialized one
from ..serializers.laundry_detail import LaundryDetailSerializer

# pyre-ignore[missing-module]
from rest_framework import serializers

logger = logging.getLogger(__name__)


class AdminLaundryApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Laundry
        fields = ["id", "name", "status", "is_active", "approved_at", "rejected_at"]
        read_only_fields = ["id", "name", "approved_at", "rejected_at"]


class AdminLaundryViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    Platform administration endpoints for vetting laundry businesses.
    """

    queryset = Laundry.objects.all().order_by("-created_at")
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]
    serializer_class = AdminLaundryApprovalSerializer

    @decorators.action(detail=True, methods=["patch"])
    def approve(self, request, pk=None):
        """Approve a laundry business. Activation is still required by the owner."""
        laundry = self.get_object()

        try:
            with transaction.atomic():
                laundry.status = Laundry.ApprovalStatus.APPROVED
                # We do NOT set is_active=True here anymore.
                # Owner must configure hours/services and call activate.
                laundry.approved_at = timezone.now()
                laundry.save()

                logger.info(f"Laundry {
                        laundry.id} approved by admin {
                        request.user.email}")
        except DjangoValidationError as e:
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": "Approval failed due to configuration errors.",
                    "data": {
                        "errors": (
                            e.message_dict if hasattr(e, "message_dict") else str(e)
                        )
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"Approval failed for laundry {laundry.id}: {str(e)}")
            return Response(
                {
                    "success": False,
                    "status": "error",
                    "message": f"An error occurred: {str(e)}",
                    "data": {},
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "success": True,
                "status": "success",
                "message": f"Laundry {laundry.name} has been approved and is now active.",
                "data": self.get_serializer(laundry).data,
            }
        )

    @decorators.action(detail=True, methods=["patch"])
    def reject(self, request, pk=None):
        """Reject a laundry business."""
        laundry = self.get_object()
        reason = request.data.get("reason", "No specific reason provided.")

        with transaction.atomic():
            laundry.status = Laundry.ApprovalStatus.REJECTED
            laundry.is_active = False
            laundry.rejected_at = timezone.now()
            laundry.save()

            logger.info(f"Laundry {
                    laundry.id} rejected by admin {
                    request.user.email}. Reason: {reason}")

        return Response(
            {
                "success": True,
                "status": "success",
                "message": f"Laundry {laundry.name} has been rejected.",
                "data": self.get_serializer(laundry).data,
            }
        )


class AdminServiceViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Platform administration endpoints for vetting and creating services.
    """

    queryset = LaundryService.objects.all().select_related(
        "laundry", "item", "category"
    )
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def get_serializer_class(self):
        from ..serializers.service import LaundryServiceSerializer

        return LaundryServiceSerializer

    @decorators.action(detail=True, methods=["patch"])
    def approve(self, request, pk=None):
        """Approve a specific service and make it available."""
        service = self.get_object()

        with transaction.atomic():
            service.is_available = True
            service.save()

            logger.info(f"LaundryService {
                    service.id} approved by admin {
                    request.user.email}")

        return Response(
            {
                "success": True,
                "status": "success",
                "message": f"Service {service.item.name} for {service.laundry.name} has been approved.",
                "data": {"id": str(service.id), "is_available": service.is_available},
            }
        )
