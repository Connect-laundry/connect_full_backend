"""Shared analytics metric computations.

Single source of truth for dashboard numbers — consumed by the DRF dashboard
endpoints (analytics/dashboards.py), the charted Django-admin page
(config/admin_analytics.py), and the exporters. All functions are read-only and
DB-aggregated; each accepts an explicit window where relevant.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum, Avg
from django.db.models.functions import TruncDate, Coalesce
from django.utils import timezone

from ordering.models import Order
from payments.models import Payment
from laundries.models.review import Review
from marketplace.models import Notification, NotificationCampaign
from .models import AnalyticsEvent

User = get_user_model()

ACTIVE_ORDER_STATUSES = ['PENDING', 'CONFIRMED', 'PICKED_UP', 'IN_PROCESS', 'OUT_FOR_DELIVERY']


def rate(num, den):
    return round((num / den) * 100, 2) if den else 0.0


def clamp_days(value, default=30, cap=365):
    try:
        return min(int(value), cap)
    except (TypeError, ValueError):
        return default


def _day_series(qs, field='created_at'):
    rows = (
        qs.annotate(day=TruncDate(field)).values('day')
        .annotate(count=Count('id')).order_by('day')
    )
    return [{'day': r['day'].isoformat(), 'count': r['count']} for r in rows if r['day']]


def executive_metrics():
    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today.replace(day=1)
    active_window = now - timedelta(minutes=30)

    revenue_today = Payment.objects.filter(
        status='SUCCESS', paid_at__gte=today
    ).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t'].quantize(Decimal('0.01'))
    revenue_month = Payment.objects.filter(
        status='SUCCESS', paid_at__gte=month_start
    ).aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t'].quantize(Decimal('0.01'))
    avg_rating = Review.objects.aggregate(a=Avg('rating'))['a']

    return {
        "active_users_now": AnalyticsEvent.objects.filter(created_at__gte=active_window)
            .exclude(session_id='').values('session_id').distinct().count(),
        "orders_today": Order.objects.filter(created_at__gte=today).count(),
        "revenue_today": str(revenue_today),
        "revenue_this_month": str(revenue_month),
        "new_users_today": User.objects.filter(created_at__gte=today, role='CUSTOMER').count(),
        "pending_orders": Order.objects.filter(status__in=ACTIVE_ORDER_STATUSES).count(),
        "notifications_sent_today": Notification.objects.filter(created_at__gte=today).count(),
        "avg_rating": round(avg_rating, 2) if avg_rating else None,
        "generated_at": now.isoformat(),
    }


def user_metrics(days=30):
    days = clamp_days(days)
    now = timezone.now()
    since = now - timedelta(days=days)
    ev = AnalyticsEvent.objects.filter(created_at__gte=since, user__isnull=False)

    def active_since(delta):
        return ev.filter(created_at__gte=now - delta).values('user').distinct().count()

    dau_rows = (
        ev.annotate(day=TruncDate('created_at')).values('day')
        .annotate(users=Count('user', distinct=True)).order_by('day')
    )
    new_users = User.objects.filter(role='CUSTOMER', created_at__gte=since)

    return {
        "window_days": days,
        "dau": active_since(timedelta(days=1)),
        "wau": active_since(timedelta(days=7)),
        "mau": active_since(timedelta(days=30)),
        "new_users": new_users.count(),
        "total_customers": User.objects.filter(role='CUSTOMER').count(),
        "daily_active_users": [
            {'day': r['day'].isoformat(), 'users': r['users']} for r in dau_rows if r['day']
        ],
        "new_users_by_day": _day_series(new_users),
        "by_platform": list(ev.values('platform').annotate(count=Count('id')).order_by('-count')),
    }


def order_metrics(days=30):
    days = clamp_days(days)
    since = timezone.now() - timedelta(days=days)
    qs = Order.objects.filter(created_at__gte=since)

    created = qs.count()
    completed = qs.filter(status__in=['DELIVERED', 'COMPLETED']).count()
    cancelled = qs.filter(status__in=['CANCELLED', 'REJECTED']).count()
    in_progress = qs.filter(status__in=ACTIVE_ORDER_STATUSES).count()
    paid = qs.filter(payment_status='PAID').count()
    aov = qs.aggregate(a=Coalesce(Avg('total_amount'), Decimal('0')))['a']

    return {
        "window_days": days,
        "created": created,
        "completed": completed,
        "cancelled": cancelled,
        "in_progress": in_progress,
        "average_order_value": str(round(aov, 2)),
        "completion_rate": rate(completed, created),
        "cancellation_rate": rate(cancelled, created),
        "orders_by_day": _day_series(qs),
        "funnel": [
            {"stage": "Created", "count": created},
            {"stage": "Paid", "count": paid},
            {"stage": "Completed", "count": completed},
        ],
    }


def revenue_metrics(days=30):
    days = clamp_days(days)
    since = timezone.now() - timedelta(days=days)
    paid = Payment.objects.filter(status='SUCCESS', paid_at__gte=since)
    attempts = Payment.objects.filter(created_at__gte=since)

    gross = paid.aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t'].quantize(Decimal('0.01'))
    fee_rate = Decimal(str(getattr(settings, 'PLATFORM_FEE_RATE', 0.05)))
    platform_revenue = (gross * fee_rate).quantize(Decimal('0.01'))

    success = paid.count()
    failed = attempts.filter(status='FAILED').count()
    total_attempts = attempts.count()

    by_day = (
        paid.annotate(day=TruncDate('paid_at')).values('day')
        .annotate(total=Coalesce(Sum('amount'), Decimal('0'))).order_by('day')
    )
    by_city = (
        paid.values('user__addresses__city')
        .annotate(total=Coalesce(Sum('amount'), Decimal('0')))
        .order_by('-total')[:10]
    )

    return {
        "window_days": days,
        "gross_revenue": str(gross),
        "platform_revenue": str(platform_revenue),
        "net_to_laundries": str((gross - platform_revenue).quantize(Decimal('0.01'))),
        "successful_payments": success,
        "failed_payments": failed,
        "payment_success_rate": rate(success, total_attempts),
        "payment_failure_rate": rate(failed, total_attempts),
        "revenue_by_day": [
            {'day': r['day'].isoformat(), 'total': str(r['total'])} for r in by_day if r['day']
        ],
        "revenue_by_city": [
            {'city': r['user__addresses__city'] or 'Unknown', 'total': str(r['total'])}
            for r in by_city
        ],
    }


def referral_metrics():
    customers = User.objects.filter(role='CUSTOMER')
    referred = customers.filter(referred_by__isnull=False)
    top = (
        customers.filter(referrals__isnull=False)
        .annotate(n=Count('referrals')).order_by('-n')[:10]
        .values('email', 'n')
    )
    total_customers = customers.count()
    return {
        "total_referred_users": referred.count(),
        "referrers": customers.filter(referrals__isnull=False).distinct().count(),
        "referral_share_of_signups": rate(referred.count(), total_customers),
        "top_referrers": [{'email': r['email'], 'referrals': r['n']} for r in top],
    }


def notification_metrics():
    agg = NotificationCampaign.objects.aggregate(
        recipients=Coalesce(Sum('recipients_count'), 0),
        delivered=Coalesce(Sum('delivered_count'), 0),
        failed=Coalesce(Sum('failed_count'), 0),
        opened=Coalesce(Sum('opened_count'), 0),
        clicked=Coalesce(Sum('clicked_count'), 0),
        converted=Coalesce(Sum('converted_count'), 0),
        revenue=Coalesce(Sum('revenue_generated'), Decimal('0')),
    )
    d, o, c, conv = agg['delivered'], agg['opened'], agg['clicked'], agg['converted']
    return {
        **{k: (str(v) if k == 'revenue' else v) for k, v in agg.items()},
        "open_rate": rate(o, d),
        "click_rate": rate(c, d),
        "conversion_rate": rate(conv, d),
        "funnel": [
            {"stage": "Delivered", "count": d},
            {"stage": "Opened", "count": o},
            {"stage": "Clicked", "count": c},
            {"stage": "Converted", "count": conv},
        ],
    }
