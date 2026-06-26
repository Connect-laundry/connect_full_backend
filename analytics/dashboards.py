"""Admin analytics dashboards (DRF).

Thin HTTP layer over analytics.metrics — read-only, admin-only aggregation
endpoints that power the executive / user / order / revenue / referral
dashboards plus CSV/Excel/PDF export. Metric computation lives in
analytics.metrics so the charted admin page reuses the exact same numbers.
"""
import csv
from datetime import timedelta

# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, decorators
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.http import HttpResponse
# pyre-ignore[missing-module]
from django.utils import timezone

from ordering.models import Order
from payments.models import Payment
from .models import AnalyticsEvent
from . import metrics
from .exports import build_rows_export


class DashboardViewSet(viewsets.GenericViewSet):
    """Admin-only analytics dashboards. All actions accept ?days=N (default 30)."""
    queryset = AnalyticsEvent.objects.none()
    permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]

    def _days(self, request):
        return metrics.clamp_days(request.query_params.get('days', 30))

    @decorators.action(detail=False, methods=['get'])
    def executive(self, request):
        return Response({"status": "success", "data": metrics.executive_metrics()})

    @decorators.action(detail=False, methods=['get'])
    def users(self, request):
        return Response({"status": "success", "data": metrics.user_metrics(self._days(request))})

    @decorators.action(detail=False, methods=['get'])
    def orders(self, request):
        return Response({"status": "success", "data": metrics.order_metrics(self._days(request))})

    @decorators.action(detail=False, methods=['get'])
    def revenue(self, request):
        return Response({"status": "success", "data": metrics.revenue_metrics(self._days(request))})

    @decorators.action(detail=False, methods=['get'])
    def referrals(self, request):
        return Response({"status": "success", "data": metrics.referral_metrics()})

    @decorators.action(detail=False, methods=['get'])
    def notifications(self, request):
        return Response({"status": "success", "data": metrics.notification_metrics()})

    @decorators.action(detail=False, methods=['get'])
    def export(self, request):
        """Tabular export. ?dataset=orders|payments|events, ?fmt=csv|xlsx|pdf, ?days=N.

        Note: the param is `fmt`, not `format` — DRF reserves `format` for
        content-negotiation (renderer selection)."""
        dataset = request.query_params.get('dataset', 'orders')
        fmt = request.query_params.get('fmt', 'csv').lower()
        days = self._days(request)
        since = timezone.now() - timedelta(days=days)

        header, rows = _dataset_rows(dataset, since)

        if fmt == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{dataset}_{days}d.csv"'
            writer = csv.writer(response)
            writer.writerow(header)
            writer.writerows(rows)
            return response

        return build_rows_export(fmt, f'{dataset}_{days}d', header, rows,
                                 title=f'{dataset.title()} — last {days} days')


def _dataset_rows(dataset, since):
    """Return (header, rows) for an export dataset, capped at 10k rows."""
    if dataset == 'payments':
        header = ['id', 'order_no', 'amount', 'currency', 'status', 'created_at']
        rows = [
            [str(p.id), getattr(p.order, 'order_no', ''), str(p.amount), p.currency,
             p.status, p.created_at.isoformat()]
            for p in Payment.objects.filter(created_at__gte=since).select_related('order')[:10000]
        ]
    elif dataset == 'events':
        header = ['event_name', 'platform', 'screen_name', 'created_at']
        rows = [
            [e.event_name, e.platform, e.screen_name, e.created_at.isoformat()]
            for e in AnalyticsEvent.objects.filter(created_at__gte=since)[:10000]
        ]
    else:  # orders
        header = ['order_no', 'status', 'payment_status', 'total_amount', 'created_at']
        rows = [
            [o.order_no, o.status, o.payment_status, str(o.total_amount), o.created_at.isoformat()]
            for o in Order.objects.filter(created_at__gte=since)[:10000]
        ]
    return header, rows
