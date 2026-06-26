"""Scheduled analytics email reports.

Builds a period KPI summary (reusing analytics.metrics) and emails it — with a
PDF summary + orders CSV attached — to ANALYTICS_REPORT_RECIPIENTS. Driven by
Celery beat (see config/settings.py CELERY_BEAT_SCHEDULE). No-ops safely when
no recipients are configured.
"""
import csv
import io
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from . import metrics
from .exports import build_summary_pdf

logger = logging.getLogger(__name__)

PERIOD_DAYS = {'daily': 1, 'weekly': 7, 'monthly': 30}


def _kpi_pairs(days):
    ex = metrics.executive_metrics()
    rev = metrics.revenue_metrics(days)
    orders = metrics.order_metrics(days)
    users = metrics.user_metrics(days)
    notif = metrics.notification_metrics()
    return [
        ("Gross revenue", f"GHS {rev['gross_revenue']}"),
        ("Platform revenue", f"GHS {rev['platform_revenue']}"),
        ("Payment success rate", f"{rev['payment_success_rate']}%"),
        ("Orders created", orders['created']),
        ("Orders completed", orders['completed']),
        ("Completion rate", f"{orders['completion_rate']}%"),
        ("Avg order value", f"GHS {orders['average_order_value']}"),
        ("New users", users['new_users']),
        ("DAU / WAU / MAU", f"{users['dau']} / {users['wau']} / {users['mau']}"),
        ("Revenue today", f"GHS {ex['revenue_today']}"),
        ("Pending orders", ex['pending_orders']),
        ("Notif. open rate", f"{notif['open_rate']}%"),
        ("Notif. conversion", f"{notif['conversion_rate']}%"),
    ]


def _orders_csv_bytes(days):
    from ordering.models import Order
    since = timezone.now() - timedelta(days=days)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['order_no', 'status', 'payment_status', 'total_amount', 'created_at'])
    for o in Order.objects.filter(created_at__gte=since)[:10000]:
        writer.writerow([o.order_no, o.status, o.payment_status, str(o.total_amount),
                         o.created_at.isoformat()])
    return buf.getvalue().encode('utf-8')


@shared_task(name="analytics.reports.email_period_report")
def email_period_report(period='daily'):
    """Email a KPI report for the given period to the configured recipients."""
    recipients = list(getattr(settings, 'ANALYTICS_REPORT_RECIPIENTS', []) or [])
    if not recipients:
        logger.info("Skipping %s analytics report: no recipients configured", period)
        return 0

    days = PERIOD_DAYS.get(period, 1)
    label = period.capitalize()
    today = timezone.now().strftime('%Y-%m-%d')
    kpis = _kpi_pairs(days)

    lines = "".join(f"<tr><td style='padding:4px 12px;'>{k}</td>"
                    f"<td style='padding:4px 12px;font-weight:600;'>{v}</td></tr>"
                    for k, v in kpis)
    html = (f"<h2>Connect Laundry — {label} Report ({today})</h2>"
            f"<p>Covering the last {days} day(s).</p>"
            f"<table style='border-collapse:collapse;'>{lines}</table>"
            f"<p style='color:#888;font-size:12px;'>Attached: PDF summary + orders CSV.</p>")

    subject = f"Connect Laundry — {label} Analytics Report ({today})"
    email = EmailMessage(
        subject=subject, body=html,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None), to=recipients,
    )
    email.content_subtype = 'html'
    try:
        email.attach(f'connect_{period}_{today}.pdf',
                     build_summary_pdf(f"Connect Laundry — {label} Report ({today})", kpis),
                     'application/pdf')
        email.attach(f'orders_{period}_{today}.csv', _orders_csv_bytes(days), 'text/csv')
    except Exception as exc:  # pragma: no cover - attachment best-effort
        logger.warning("Report attachment build failed: %s", exc)

    sent = email.send(fail_silently=True)
    logger.info("Analytics %s report sent to %d recipient(s) (result=%s)",
                period, len(recipients), sent)
    return sent
