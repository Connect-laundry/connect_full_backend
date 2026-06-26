# pyre-ignore[missing-module]
from datetime import timedelta

# pyre-ignore[missing-module]
from rest_framework import status, permissions
# pyre-ignore[missing-module]
from rest_framework.views import APIView
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db.models import Count
# pyre-ignore[missing-module]
from django.db.models.functions import TruncDate
# pyre-ignore[missing-module]
from django.utils import timezone

from config.throttling import NotifTrackThrottle
from .models import AnalyticsEvent
from .serializers import AnalyticsBatchSerializer
from .services import AnalyticsService


class AnalyticsIngestThrottle(NotifTrackThrottle):
    """Per-user batch-ingest limit (reuses the high-volume tracking scope)."""
    scope = 'notif_track'


class AnalyticsIngestView(APIView):
    """POST a batch of client events. Authenticated; events are attributed to
    the requesting user and PII-redacted before persistence."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AnalyticsIngestThrottle]

    def post(self, request):
        serializer = AnalyticsBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        events = serializer.validated_data['events']

        created = 0
        for ev in events:
            result = AnalyticsService.record(
                ev['event_name'],
                user=request.user,
                session_id=ev.get('session_id', ''),
                device_id=ev.get('device_id', ''),
                platform=ev.get('platform', AnalyticsEvent.Platform.UNKNOWN),
                os_version=ev.get('os_version', ''),
                app_version=ev.get('app_version', ''),
                screen_name=ev.get('screen_name', ''),
                event_data=ev.get('event_data', {}),
                occurred_at=ev.get('occurred_at'),
            )
            if result:
                created += 1

        return Response(
            {"status": "success", "message": "Events recorded", "data": {"accepted": created}},
            status=status.HTTP_201_CREATED,
        )


class AnalyticsSummaryView(APIView):
    """Admin-only summary metrics for the analytics dashboard.

    Lightweight aggregation over a date window (default 30 days): event totals,
    a daily active-users curve, and a top-events breakdown. Heavy cohort /
    materialized-view work is layered on top of this in later phases.
    """
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def get(self, request):
        try:
            days = min(int(request.query_params.get('days', 30)), 365)
        except (TypeError, ValueError):
            days = 30
        since = timezone.now() - timedelta(days=days)
        qs = AnalyticsEvent.objects.filter(created_at__gte=since)

        total_events = qs.count()
        dau = (
            qs.filter(user__isnull=False)
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(users=Count('user', distinct=True))
            .order_by('day')
        )
        top_events = (
            qs.values('event_name')
            .annotate(count=Count('id'))
            .order_by('-count')[:20]
        )
        by_platform = (
            qs.values('platform').annotate(count=Count('id')).order_by('-count')
        )

        return Response({"status": "success", "data": {
            "window_days": days,
            "total_events": total_events,
            "unique_users": qs.filter(user__isnull=False).values('user').distinct().count(),
            "active_sessions": qs.exclude(session_id='').values('session_id').distinct().count(),
            "daily_active_users": [
                {"day": row['day'].isoformat(), "users": row['users']} for row in dau
            ],
            "top_events": list(top_events),
            "by_platform": list(by_platform),
        }})
