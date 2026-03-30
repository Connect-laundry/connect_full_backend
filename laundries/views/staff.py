from rest_framework import viewsets, status, decorators
from rest_framework.response import Response
import logging

from ..models.staff import LaundryStaff
from ..models.laundry import Laundry
from ..serializers.staff import LaundryStaffSerializer, StaffInviteSerializer, StaffRoleUpdateSerializer
from .owner import IsOwner

logger = logging.getLogger(__name__)


class StaffViewSet(viewsets.ModelViewSet):
    """
    Manage staff members for a laundry (owner-scoped).

    - GET    /  → List all staff
    - POST   /invite/ → Invite a new staff member
    - PATCH  /{id}/role/ → Reassign a staff member's role
    - DELETE /{id}/ → Remove a staff member
    """
    serializer_class = LaundryStaffSerializer
    permission_classes = [IsOwner]

    def _get_laundry(self, request):
        return Laundry.objects.filter(owner=request.user).first()

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return LaundryStaff.objects.none()
        laundry = self._get_laundry(self.request)
        if not laundry:
            return LaundryStaff.objects.none()
        return LaundryStaff.objects.filter(laundry=laundry)

    @decorators.action(detail=False, methods=['post'], url_path='invite')
    def invite(self, request):
        """Invite a new staff member via email/phone."""
        laundry = self._get_laundry(request)
        if not laundry:
            return Response({
                "success": False,
                "message": "You must create a laundry first."
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer = StaffInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Check for duplicate
        if LaundryStaff.objects.filter(
            laundry=laundry, email=serializer.validated_data['email']
        ).exists():
            return Response({
                "success": False,
                "message": "This person has already been invited."
            }, status=status.HTTP_400_BAD_REQUEST)

        staff = LaundryStaff.objects.create(
            laundry=laundry,
            name=serializer.validated_data['name'],
            email=serializer.validated_data['email'],
            phone=serializer.validated_data.get('phone', ''),
            role=serializer.validated_data['role'],
            invite_status=LaundryStaff.InviteStatus.PENDING,
        )

        logger.info(
            f"Staff invite sent to {
                staff.email} for laundry {
                laundry.id} by {
                request.user.email}")

        return Response({
            "success": True,
            "message": f"Invitation sent to {staff.email}.",
            "data": LaundryStaffSerializer(staff).data
        }, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=['patch'], url_path='role')
    def update_role(self, request, pk=None):
        """Reassign a staff member's role."""
        staff = self.get_object()
        serializer = StaffRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        staff.role = serializer.validated_data['role']
        staff.save(update_fields=['role', 'updated_at'])

        logger.info(
            f"Staff {
                staff.id} role → {
                staff.role} by {
                request.user.email}")

        return Response({
            "success": True,
            "message": f"{staff.name}'s role updated to {staff.get_role_display()}.",
            "data": LaundryStaffSerializer(staff).data
        })
