# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, decorators, serializers, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
# pyre-ignore[missing-module]
from config.throttling import NotifTrackThrottle, TestPushThrottle
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.db import transaction
from django.conf import settings
# pyre-ignore[missing-module]
from marketplace.models import Notification, PushDevice, NotificationPreference
from marketplace.services.customer_events import CUSTOMER_EVENT_TEMPLATES, notify_customer_event
# pyre-ignore[missing-module]
from ..serializers import (
    NotificationSerializer, PushDeviceSerializer, NotificationPreferenceSerializer,
)
import logging

logger = logging.getLogger(__name__)


class NotificationPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100

class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for checking and managing user notifications.
    """
    queryset = Notification.objects.none()
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        queryset = Notification.objects.filter(
            user=self.request.user, audience=Notification.Audience.USER
        )
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            queryset = queryset.filter(is_read=is_read.lower() == 'true')
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data['unread_count'] = unread_count
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "status": "success",
            "message": "Notifications fetched",
            "data": {
                "count": queryset.count(),
                "unread_count": unread_count,
                "results": serializer.data
            }
        })

    @decorators.action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        """Get the number of unread notifications."""
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({
            "status": "success",
            "unread_count": unread_count
        })

    @decorators.action(detail=True, methods=['patch'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        """Mark a single notification as read."""
        notification = self.get_object()
        notification.mark_as_read()
        serializer = self.get_serializer(notification)
        return Response({
            "status": "success",
            "message": "Notification marked as read",
            "data": serializer.data
        })

    @decorators.action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        """Mark all unread notifications as read for current user."""
        Notification.objects.filter(user=request.user, is_read=False).update(
            is_read=True, 
            read_at=timezone.now()
        )
        return Response({
            "status": "success",
            "message": "All notifications marked as read"
        })

    @decorators.action(detail=False, methods=['get'], url_path='push-diagnostics')
    def push_diagnostics(self, request):
        """Why did (or didn't) my phone get a push? Own account only.

        Lists this user's registered devices for this server's push
        environment and the delivery outcome of recent pushes, including the
        APNs/FCM error code from Expo's receipt. Token values are not returned.
        """
        from marketplace.models import PushDelivery
        environment = getattr(settings, 'PUSH_ENVIRONMENT', 'staging')
        devices = PushDevice.objects.filter(user=request.user).order_by('-last_registered_at')[:10]
        recent = (Notification.objects.filter(user=request.user, audience=Notification.Audience.USER)
                  .exclude(push_status=Notification.PushStatus.NONE).order_by('-created_at')[:10])
        deliveries = {}
        for d in PushDelivery.objects.filter(notification__in=list(recent)).order_by('created_at'):
            deliveries.setdefault(d.notification_id, []).append({
                'status': d.status, 'error_code': d.error_code or None,
                'receipt_checked_at': d.receipt_checked_at,
            })
        return Response({
            'status': 'success',
            'data': {
                'server_environment': environment,
                'push_enabled': bool(getattr(settings, 'EXPO_PUSH_ENABLED', False)),
                'devices': [{
                    'platform': d.platform, 'environment': d.environment, 'is_active': d.is_active,
                    'matches_server': d.environment == environment,
                    'app_version': d.app_version, 'last_registered_at': d.last_registered_at,
                } for d in devices],
                'recent_pushes': [{
                    'title': n.title, 'category': n.category, 'push_status': n.push_status,
                    'created_at': n.created_at, 'deliveries': deliveries.get(n.id, []),
                } for n in recent],
            },
        })

    @decorators.action(detail=False, methods=['post'], url_path='test-push', throttle_classes=[TestPushThrottle])
    def test_push(self, request):
        """Send one test notification to the caller's own devices."""
        from marketplace.services.notification_service import NotificationService
        environment = getattr(settings, 'PUSH_ENVIRONMENT', 'staging')
        active = PushDevice.objects.filter(user=request.user, environment=environment, is_active=True).count()
        if not active:
            return Response({
                'status': 'error',
                'message': 'This phone is not registered for notifications yet. Allow notifications for Simame in Settings, reopen the app and try again.',
                'data': {'active_devices': 0},
            }, status=status.HTTP_409_CONFLICT)
        notification = NotificationService.notify_user(
            request.user, 'Notifications are working', 'This is a test from Simame. You can ignore it.',
            type=Notification.Type.SYSTEM, category='SYSTEM', priority=Notification.Priority.URGENT,
            dedup_key=f'test-push:{timezone.now().timestamp():.0f}',
        )
        return Response({
            'status': 'success',
            'message': 'Test notification sent. It should arrive within a few seconds.',
            'data': {'active_devices': active, 'notification_id': str(notification.id), 'push_status': notification.push_status},
        })

    @decorators.action(
        detail=True, methods=['post'], url_path='track',
        throttle_classes=[NotifTrackThrottle],
    )
    def track(self, request, pk=None):
        """Record an engagement event for a notification.

        Body: {"event": "opened" | "clicked"}. Idempotent — repeated events
        only count once. Drives campaign open/click analytics. Scoped to the
        requesting user's own notifications via get_object()/get_queryset().
        """
        notification = self.get_object()
        event = (request.data.get('event') or 'opened').lower()
        if event == 'clicked':
            notification.mark_clicked()
        else:
            notification.mark_opened()
        return Response({
            "status": "success",
            "message": f"Notification {event} recorded",
            "data": {
                "id": str(notification.id),
                "opened_at": notification.opened_at,
                "clicked_at": notification.clicked_at,
            },
        })
    @decorators.action(detail=False, methods=['get', 'patch'], url_path='preferences')
    def preferences(self, request):
        """Get or update the current user's notification preferences."""
        pref, _ = NotificationPreference.objects.get_or_create(user=request.user)

        if request.method == 'PATCH':
            serializer = NotificationPreferenceSerializer(pref, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response({
                "status": "success",
                "message": "Preferences updated",
                "data": serializer.data,
            })

        return Response({
            "status": "success",
            "data": NotificationPreferenceSerializer(pref).data,
        })

    @decorators.action(detail=False, methods=['post', 'delete'], url_path='push-device')
    def push_device(self, request):
        # The server is the authority on its own environment. A build that
        # claims another one is misconfigured and must not be registered here.
        push_environment = getattr(settings, 'PUSH_ENVIRONMENT', 'staging')
        claimed_environment = request.data.get('environment')
        if claimed_environment and claimed_environment != push_environment:
            logger.warning(
                "Rejected cross-environment push registration",
                extra={"claimed": claimed_environment, "server": push_environment},
            )
            return Response(
                {'environment': 'This app build targets a different push environment.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        """Register, refresh, or deactivate the user's Expo push token."""
        if request.method == 'DELETE':
            token = request.data.get('token')
            device_id = request.data.get('device_id')
            qs = PushDevice.objects.filter(
                user=request.user, environment=push_environment, is_active=True,
            )
            if token and device_id:
                qs = qs.filter(token=token, device_id=device_id)
            elif token:
                qs = qs.filter(token=token)
            elif device_id:
                qs = qs.filter(device_id=device_id)
            qs.update(is_active=False)
            return Response({
                "status": "success",
                "message": "Push device unregistered",
            })

        serializer = PushDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data['token']
        defaults = {
            'user': request.user,
            'environment': push_environment,
            'device_id': serializer.validated_data.get('device_id', ''),
            'platform': serializer.validated_data.get('platform', PushDevice.Platform.UNKNOWN),
            'app_version': serializer.validated_data.get('app_version', ''),
            'is_active': True,
        }
        # Expo tokens are globally unique in our schema. Look up globally so a
        # rebuilt app can safely move a token between staging and production
        # without violating that constraint; sends remain server-environment
        # scoped.
        existing = PushDevice.objects.filter(token=token).only('user_id').first()
        if existing and existing.user_id != request.user.id:
            logger.warning(
                "Push token reassigned to a different user",
                extra={
                    "previous_user_id": str(existing.user_id),
                    "new_user_id": str(request.user.id),
                },
            )
        device_id = defaults['device_id']
        with transaction.atomic():
            if device_id:
                PushDevice.objects.select_for_update().filter(
                    device_id=device_id,
                    environment=push_environment,
                    is_active=True,
                ).exclude(token=token).update(is_active=False)
            device, _ = PushDevice.objects.update_or_create(token=token, defaults=defaults)
        return Response({
            "status": "success",
            "message": "Push device registered",
            "data": PushDeviceSerializer(device).data,
        }, status=status.HTTP_200_OK)

    @decorators.action(detail=False, methods=['post'], url_path='auth-event')
    def auth_event(self, request):
        """Record friendly auth notifications after the app session is usable."""
        class AuthEventSerializer(serializers.Serializer):
            event = serializers.ChoiceField(choices=[
                ('SIGNUP_SUCCESS', 'SIGNUP_SUCCESS'),
                ('LOGIN_SUCCESS', 'LOGIN_SUCCESS'),
                ('NEW_DEVICE_LOGIN', 'NEW_DEVICE_LOGIN'),
                ('PASSWORD_CHANGED', 'PASSWORD_CHANGED'),
            ])

        serializer = AuthEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = serializer.validated_data['event']
        if event not in CUSTOMER_EVENT_TEMPLATES:
            return Response(
                {"status": "error", "message": "Unsupported notification event."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        device_id = (request.META.get('HTTP_X_DEVICE_ID') or 'unknown-device')[:128]
        idempotency_key = (
            request.META.get('HTTP_X_IDEMPOTENCY_KEY')
            or request.META.get('HTTP_X_REQUEST_ID')
            or str(request.user.id)
        )[:128]
        notification = notify_customer_event(
            request.user,
            event,
            dedup_key=f'auth:{event}:{device_id}:{idempotency_key}',
        )

        if event == 'LOGIN_SUCCESS':
            new_device_key = f'auth:NEW_DEVICE_LOGIN:{device_id}'
            if not Notification.objects.filter(
                user=request.user,
                audience=Notification.Audience.USER,
                dedup_key=new_device_key,
            ).exists():
                notify_customer_event(
                    request.user,
                    'NEW_DEVICE_LOGIN',
                    dedup_key=new_device_key,
                )

        return Response({
            "status": "success",
            "message": "Notification recorded",
            "data": {"id": str(notification.id), "event": event},
        }, status=status.HTTP_201_CREATED)
