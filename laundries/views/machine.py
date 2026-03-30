from rest_framework import viewsets, status, decorators
from rest_framework.response import Response
import logging

from ..models.machine import Machine
from ..models.laundry import Laundry
from ..serializers.machine import MachineSerializer, MachineStatusSerializer
from .owner import IsOwner

logger = logging.getLogger(__name__)


class MachineViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for laundry machines (owner-scoped).

    - GET    /  → List all machines for the owner's laundry
    - POST   /  → Register a new machine
    - PATCH  /{id}/ → Update machine details
    - DELETE /{id}/ → Remove a machine
    - PATCH  /{id}/status/ → Quick status toggle (Idle/Busy/Maintenance)
    """

    serializer_class = MachineSerializer
    permission_classes = [IsOwner]

    def _get_laundry(self, request):
        return Laundry.objects.filter(owner=request.user).first()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Machine.objects.none()
        laundry = self._get_laundry(self.request)
        if not laundry:
            return Machine.objects.none()
        return Machine.objects.filter(laundry=laundry)

    def perform_create(self, serializer):
        laundry = self._get_laundry(self.request)
        if not laundry:
            raise ValueError("No laundry found for this owner.")
        serializer.save(laundry=laundry)

    def create(self, request, *args, **kwargs):
        laundry = self._get_laundry(request)
        if not laundry:
            return Response(
                {"success": False, "message": "You must create a laundry first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        logger.info(f"Machine '{
                serializer.data.get('name')}' registered by {
                request.user.email}")

        return Response(
            {
                "success": True,
                "message": "Machine registered successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    @decorators.action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        """Quick status toggle for a machine."""
        machine = self.get_object()
        serializer = MachineStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        machine.status = serializer.validated_data["status"]
        machine.save(update_fields=["status", "updated_at"])

        logger.info(f"Machine {
                machine.id} status → {
                machine.status} by {
                request.user.email}")

        return Response(
            {
                "success": True,
                "message": f"Machine status updated to {machine.get_status_display()}.",
                "data": MachineSerializer(machine).data,
            }
        )
