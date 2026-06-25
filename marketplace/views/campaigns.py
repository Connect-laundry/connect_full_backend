"""Admin Campaign Center API.

Staff-only endpoints to create, list, preview, send, schedule and analyse
notification campaigns. Sending is delegated to Celery (run_campaign) so the
request returns immediately; scheduling sets status=SCHEDULED and lets the
beat task `process_scheduled_campaigns` execute it when due.
"""
# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, decorators, status
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db.models import Sum
# pyre-ignore[missing-module]
from django.utils import timezone
import logging

from marketplace.models import Notification, NotificationCampaign
from marketplace.serializers import NotificationCampaignSerializer
from marketplace.services.campaign_service import CampaignService
from marketplace.services.audit import record_audit

logger = logging.getLogger(__name__)


class CampaignViewSet(viewsets.ModelViewSet):
    """CRUD + send/schedule/preview/analytics for notification campaigns."""
    queryset = NotificationCampaign.objects.all()
    serializer_class = NotificationCampaignSerializer
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @decorators.action(detail=False, methods=['post'], url_path='preview')
    def preview(self, request):
        """Return the audience size for a segment without sending anything."""
        segment = request.data.get('segment', NotificationCampaign.Segment.ALL)
        params = request.data.get('segment_params', {}) or {}
        try:
            count = CampaignService.resolve_recipients(segment, params).count()
        except Exception as exc:
            return Response(
                {"status": "error", "message": f"Invalid segment: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"status": "success", "data": {"segment": segment, "audience_size": count}})

    @decorators.action(detail=True, methods=['post'], url_path='send')
    def send(self, request, pk=None):
        """Send a campaign now (async via Celery)."""
        campaign = self.get_object()
        if campaign.status == NotificationCampaign.Status.SENDING:
            return Response(
                {"status": "error", "message": "Campaign is already sending."},
                status=status.HTTP_409_CONFLICT,
            )
        campaign.status = NotificationCampaign.Status.SCHEDULED
        campaign.scheduled_for = timezone.now()
        campaign.save(update_fields=['status', 'scheduled_for'])

        from marketplace.tasks import run_campaign
        run_campaign.delay(str(campaign.id))

        record_audit(
            action='campaign.send', request=request,
            target_type='NotificationCampaign', target_id=str(campaign.id),
            target_repr=campaign.name, metadata={'segment': campaign.segment},
        )
        return Response({"status": "success", "message": "Campaign queued for delivery."})

    @decorators.action(detail=True, methods=['post'], url_path='schedule')
    def schedule(self, request, pk=None):
        """Schedule a campaign for a future time. Body: {"scheduled_for": ISO8601}."""
        campaign = self.get_object()
        when = request.data.get('scheduled_for')
        if not when:
            return Response(
                {"status": "error", "message": "scheduled_for is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from django.utils.dateparse import parse_datetime
        parsed = parse_datetime(when)
        if parsed is None:
            return Response(
                {"status": "error", "message": "scheduled_for must be ISO-8601."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        campaign.scheduled_for = parsed
        campaign.status = NotificationCampaign.Status.SCHEDULED
        campaign.save(update_fields=['scheduled_for', 'status'])

        record_audit(
            action='campaign.schedule', request=request,
            target_type='NotificationCampaign', target_id=str(campaign.id),
            target_repr=campaign.name, metadata={'scheduled_for': parsed.isoformat()},
        )
        return Response({
            "status": "success",
            "message": "Campaign scheduled.",
            "data": {"scheduled_for": parsed},
        })

    @decorators.action(detail=True, methods=['get'], url_path='analytics')
    def analytics(self, request, pk=None):
        """Per-campaign delivery + engagement metrics."""
        c = self.get_object()
        return Response({"status": "success", "data": {
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "recipients": c.recipients_count,
            "delivered": c.delivered_count,
            "skipped": c.skipped_count,
            "failed": c.failed_count,
            "opened": c.opened_count,
            "clicked": c.clicked_count,
            "converted": c.converted_count,
            "revenue_generated": str(c.revenue_generated),
            "delivery_rate": c.delivery_rate,
            "open_rate": c.open_rate,
            "click_rate": c.click_rate,
            "failure_rate": c.failure_rate,
            "conversion_rate": c.conversion_rate,
        }})

    @decorators.action(detail=False, methods=['get'], url_path='analytics-overview')
    def analytics_overview(self, request):
        """Platform-wide notification analytics for the admin dashboard."""
        agg = NotificationCampaign.objects.aggregate(
            recipients=Sum('recipients_count'),
            delivered=Sum('delivered_count'),
            failed=Sum('failed_count'),
            opened=Sum('opened_count'),
            clicked=Sum('clicked_count'),
            converted=Sum('converted_count'),
            revenue=Sum('revenue_generated'),
        )
        recipients = agg['recipients'] or 0
        delivered = agg['delivered'] or 0
        failed = agg['failed'] or 0
        opened = agg['opened'] or 0
        clicked = agg['clicked'] or 0
        converted = agg['converted'] or 0
        revenue = agg['revenue'] or 0

        def rate(n, d):
            return round((n / d) * 100, 2) if d else 0.0

        return Response({"status": "success", "data": {
            "campaigns_total": NotificationCampaign.objects.count(),
            "campaigns_sent": NotificationCampaign.objects.filter(
                status=NotificationCampaign.Status.SENT).count(),
            "notifications_total": Notification.objects.count(),
            "push_sent": Notification.objects.filter(
                push_status=Notification.PushStatus.SENT).count(),
            "push_failed": Notification.objects.filter(
                push_status=Notification.PushStatus.FAILED).count(),
            "recipients": recipients,
            "delivered": delivered,
            "failed": failed,
            "opened": opened,
            "clicked": clicked,
            "converted": converted,
            "revenue_generated": str(revenue),
            "delivery_rate": rate(delivered, recipients),
            "open_rate": rate(opened, delivered),
            "click_rate": rate(clicked, delivered),
            "failure_rate": rate(failed, recipients),
            "conversion_rate": rate(converted, delivered),
        }})
