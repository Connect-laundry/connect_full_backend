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

from django.db import transaction
from rest_framework import viewsets
from users.models import User
from ..models.laundry import Laundry, OwnerAuditLog
from ..models.opening_hours import OpeningHours, HolidayOverride
from ..serializers.my_laundry import MyLaundrySerializer, HolidayOverrideSerializer, CopyTodayHoursSerializer, ToggleVacationModeResponseSerializer
from .pricing import get_owner_laundry

from ..permissions import IsOwnerRole
from ..renderers import StandardResponseRenderer

logger = logging.getLogger(__name__)



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


class CopyMondayHoursView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = MyLaundrySerializer

    @extend_schema(
        request=None,
        responses={200: MyLaundrySerializer}
    )
    def post(self, request):
        laundry = get_owner_laundry(request.user)
        if not laundry:
            return Response(
                {'status': 'error', 'message': 'Register a laundry before configuring hours.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        monday_hours = OpeningHours.objects.filter(laundry=laundry, day=1).first()
        if not monday_hours:
            return Response(
                {'status': 'error', 'message': 'No schedule configured for Monday.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        weekdays = [2, 3, 4, 5]  # Tue, Wed, Thu, Fri
        with transaction.atomic():
            for day in weekdays:
                OpeningHours.objects.update_or_create(
                    laundry=laundry,
                    day=day,
                    defaults={
                        'opening_time': monday_hours.opening_time,
                        'closing_time': monday_hours.closing_time,
                        'is_closed': monday_hours.is_closed,
                        'is_overnight': monday_hours.is_overnight
                    }
                )
            OwnerAuditLog.objects.create(
                laundry=laundry,
                actor=request.user,
                action='COPY_MONDAY_HOURS',
                details={}
            )
            
        return Response({
            'status': 'success',
            'message': 'Monday schedule applied to all weekdays.',
            'data': MyLaundrySerializer(laundry, context={'request': request}).data
        })


class CopyTodayHoursView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = CopyTodayHoursSerializer

    @extend_schema(
        request=CopyTodayHoursSerializer,
        responses={200: MyLaundrySerializer}
    )
    def post(self, request):
        laundry = get_owner_laundry(request.user)
        if not laundry:
            return Response(
                {'status': 'error', 'message': 'Register a laundry before configuring hours.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            today_day = int(request.data.get('day'))
            if today_day not in range(1, 8):
                raise ValueError()
        except (TypeError, ValueError):
            return Response(
                {'status': 'error', 'message': 'Please provide a valid day index (1=Mon ... 7=Sun) in the "day" field.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        today_hours = OpeningHours.objects.filter(laundry=laundry, day=today_day).first()
        if not today_hours:
            return Response(
                {'status': 'error', 'message': f'No schedule configured for day {today_day}.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        all_days = [d for d in range(1, 8) if d != today_day]
        with transaction.atomic():
            for day in all_days:
                OpeningHours.objects.update_or_create(
                    laundry=laundry,
                    day=day,
                    defaults={
                        'opening_time': today_hours.opening_time,
                        'closing_time': today_hours.closing_time,
                        'is_closed': today_hours.is_closed,
                        'is_overnight': today_hours.is_overnight
                    }
                )
            OwnerAuditLog.objects.create(
                laundry=laundry,
                actor=request.user,
                action='COPY_TODAY_HOURS',
                details={'source_day': today_day}
            )
            
        return Response({
            'status': 'success',
            'message': f"Day {today_day}'s schedule applied to all days.",
            'data': MyLaundrySerializer(laundry, context={'request': request}).data
        })


class ToggleVacationModeView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = ToggleVacationModeResponseSerializer

    @extend_schema(
        request=None,
        responses={200: ToggleVacationModeResponseSerializer}
    )
    def post(self, request):
        laundry = get_owner_laundry(request.user)
        if not laundry:
            return Response(
                {'status': 'error', 'message': 'Register a laundry first.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        laundry.vacation_mode = not laundry.vacation_mode
        laundry.save(update_fields=['vacation_mode', 'updated_at'])
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='TOGGLE_VACATION_MODE',
            details={'vacation_mode': laundry.vacation_mode}
        )
        
        return Response({
            'status': 'success',
            'message': f"Vacation mode {'enabled' if laundry.vacation_mode else 'disabled'}.",
            'data': {'vacation_mode': laundry.vacation_mode}
        })


class HolidayOverrideViewSet(viewsets.ModelViewSet):
    queryset = HolidayOverride.objects.none()
    permission_classes = [permissions.IsAuthenticated, IsOwnerRole]
    renderer_classes = [StandardResponseRenderer]
    serializer_class = HolidayOverrideSerializer

    def get_queryset(self):
        return HolidayOverride.objects.filter(laundry__owner=self.request.user)

    def _require_laundry(self):
        laundry = get_owner_laundry(self.request.user)
        if laundry is None:
            return None, Response(
                {'status': 'error', 'message': 'Register a laundry before managing holiday overrides.', 'data': None},
                status=status.HTTP_400_BAD_REQUEST
            )
        return laundry, None

    def create(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        override = serializer.save(laundry=laundry)
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='CREATE_HOLIDAY_OVERRIDE',
            details={'override_id': str(override.id), 'date': str(override.date)}
        )
        
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        response = super().update(request, *args, **kwargs)
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='UPDATE_HOLIDAY_OVERRIDE',
            details={'override_id': str(kwargs.get('pk'))}
        )
        return response

    def destroy(self, request, *args, **kwargs):
        laundry, error = self._require_laundry()
        if error is not None:
            return error
            
        instance = self.get_object()
        
        OwnerAuditLog.objects.create(
            laundry=laundry,
            actor=request.user,
            action='DELETE_HOLIDAY_OVERRIDE',
            details={'override_id': str(instance.id), 'date': str(instance.date)}
        )
        return super().destroy(request, *args, **kwargs)

