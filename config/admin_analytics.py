"""Charted analytics dashboard rendered inside the Django (unfold) admin.

A single staff-only page that surfaces the same numbers as the DRF dashboard
endpoints (via analytics.metrics) as KPI cards + Chart.js charts, with a date
window selector and CSV/Excel/PDF export buttons.
"""
import defusedcsv.csv as defused_csv
import json
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from analytics import metrics
from analytics.dashboards import _dataset_rows
from analytics.exports import build_rows_export


@staff_member_required
def analytics_dashboard_view(request):
    try:
        days = min(int(request.GET.get('days', 30)), 365)
    except (TypeError, ValueError):
        days = 30

    executive = metrics.executive_metrics()
    users = metrics.user_metrics(days)
    orders = metrics.order_metrics(days)
    revenue = metrics.revenue_metrics(days)
    referrals = metrics.referral_metrics()
    notifications = metrics.notification_metrics()

    context = {
        **_unfold_context(request),
        "title": "Analytics Dashboard",
        "days": days,
        "day_options": [7, 14, 30, 60, 90],
        "executive": executive,
        "users": users,
        "orders": orders,
        "revenue": revenue,
        "referrals": referrals,
        "notifications": notifications,
        # JSON blobs consumed by Chart.js in the template.
        "charts_json": json.dumps({
            "dau": users["daily_active_users"],
            "new_users": users["new_users_by_day"],
            "orders_by_day": orders["orders_by_day"],
            "revenue_by_day": revenue["revenue_by_day"],
            "order_funnel": orders["funnel"],
            "notification_funnel": notifications["funnel"],
            "revenue_by_city": revenue["revenue_by_city"],
            "by_platform": users["by_platform"],
        }),
    }
    return render(request, "admin/analytics_dashboard.html", context)


@staff_member_required
def analytics_export_view(request):
    """Session-authenticated export for the admin page buttons.
    ?dataset=orders|payments|events, ?format=csv|xlsx|pdf, ?days=N."""
    dataset = request.GET.get('dataset', 'orders')
    fmt = request.GET.get('fmt', request.GET.get('format', 'csv')).lower()
    try:
        days = min(int(request.GET.get('days', 30)), 365)
    except (TypeError, ValueError):
        days = 30
    since = timezone.now() - timedelta(days=days)
    header, rows = _dataset_rows(dataset, since)

    if fmt == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{dataset}_{days}d.csv"'
        writer = defused_csv.writer(response)
        writer.writerow(header)
        writer.writerows(rows)
        return response

    return build_rows_export(fmt, f'{dataset}_{days}d', header, rows,
                             title=f'{dataset.title()} — last {days} days')


def _unfold_context(request):
    """Provide the bits the unfold base template expects (site header, etc.)."""
    try:
        from django.contrib import admin
        return admin.site.each_context(request)
    except Exception:  # pragma: no cover - defensive
        return {}
