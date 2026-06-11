"""Owner-facing "My Laundry" endpoints (self-service shop management)."""
import logging

# pyre-ignore[missing-module]
from rest_framework import permissions, status
# pyre-ignore[missing-module]
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from users.models import User
from ..models.laundry import Laundry
from ..serializers.my_laundry import MyLaundrySerializer

logger = logging.getLogger(__name__)


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


def _owner_laundry_queryset(user):
    return (
        Laundry.objects.filter(owner=user)
        .prefetch_related('opening_hours')
    )


class MyLaundryView(APIView):
    """
    GET  /api/v1/laundries/dashboard/my-laundry/  -> the owner's laundry
    POST /api/v1/laundries/dashboard/my-laundry/  -> register a laundry
    """

    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = MyLaundrySerializer

    @extend_schema(responses=MyLaundrySerializer)
    def get(self, request):
        laundry = _owner_laundry_queryset(request.user).first()
        if laundry is None:
            return Response(
                {
                    "status": "error",
                    "message": "You have not registered a laundry yet.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = MyLaundrySerializer(laundry, context={'request': request})
        return Response(
            {
                "status": "success",
                "message": "Laundry retrieved successfully.",
                "data": serializer.data,
            }
        )

    @extend_schema(request=MyLaundrySerializer, responses=MyLaundrySerializer)
    def post(self, request):
        if Laundry.objects.filter(owner=request.user).exists():
            return Response(
                {
                    "status": "error",
                    "message": "You already have a registered laundry.",
                    "data": None,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MyLaundrySerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        laundry = serializer.save()
        logger.info(
            "Laundry registered by owner",
            extra={"laundry_id": str(laundry.id), "owner_id": str(request.user.id)},
        )
        output = MyLaundrySerializer(laundry, context={'request': request})
        return Response(
            {
                "status": "success",
                "message": "Laundry registered successfully and is pending approval.",
                "data": output.data,
            },
            status=status.HTTP_201_CREATED,
        )


class MyLaundryDetailView(APIView):
    """
    PATCH /api/v1/laundries/dashboard/my-laundry/<id>/ -> update profile fields
    """

    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = MyLaundrySerializer

    def _get_owned_laundry(self, request, laundry_id):
        return _owner_laundry_queryset(request.user).filter(id=laundry_id).first()

    @extend_schema(request=MyLaundrySerializer, responses=MyLaundrySerializer)
    def patch(self, request, id):
        laundry = self._get_owned_laundry(request, id)
        if laundry is None:
            # 404 (not 403) so we never disclose other owners' laundry ids.
            return Response(
                {
                    "status": "error",
                    "message": "Laundry not found.",
                    "data": None,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = MyLaundrySerializer(
            laundry, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        laundry = serializer.save()
        logger.info(
            "Laundry updated by owner",
            extra={"laundry_id": str(laundry.id), "owner_id": str(request.user.id)},
        )
        return Response(
            {
                "status": "success",
                "message": "Laundry updated successfully.",
                "data": serializer.data,
            }
        )
