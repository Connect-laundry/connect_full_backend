# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, decorators, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from marketplace.models import Notification
# pyre-ignore[missing-module]
from ..serializers import NotificationSerializer

class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for checking and managing user notifications.
    """
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Notification.objects.filter(user=self.request.user)
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
