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
from laundries.models.laundry import Laundry
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


def user_metrics(days=30, city=None, laundry_id=None):
    days = clamp_days(days)
    now = timezone.now()
    since = now - timedelta(days=days)
    ev = AnalyticsEvent.objects.filter(created_at__gte=since, user__isnull=False)
    if city:
        ev = ev.filter(user__addresses__city__iexact=city).distinct()

    def active_since(delta):
        return ev.filter(created_at__gte=now - delta).values('user').distinct().count()

    dau_rows = (
        ev.annotate(day=TruncDate('created_at')).values('day')
        .annotate(users=Count('user', distinct=True)).order_by('day')
    )
    new_users = User.objects.filter(role='CUSTOMER', created_at__gte=since)
    if city:
        new_users = new_users.filter(addresses__city__iexact=city).distinct()

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


def _apply_order_filters(qs, city=None, laundry_id=None):
    if laundry_id:
        qs = qs.filter(laundry_id=laundry_id)
    if city:
        qs = qs.filter(laundry__city__iexact=city)
    return qs


def order_metrics(days=30, city=None, laundry_id=None):
    days = clamp_days(days)
    since = timezone.now() - timedelta(days=days)
    qs = _apply_order_filters(Order.objects.filter(created_at__gte=since), city, laundry_id)

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


def revenue_metrics(days=30, city=None, laundry_id=None):
    days = clamp_days(days)
    since = timezone.now() - timedelta(days=days)
    paid = Payment.objects.filter(status='SUCCESS', paid_at__gte=since)
    attempts = Payment.objects.filter(created_at__gte=since)
    if laundry_id:
        paid = paid.filter(order__laundry_id=laundry_id)
        attempts = attempts.filter(order__laundry_id=laundry_id)
    if city:
        paid = paid.filter(order__laundry__city__iexact=city)
        attempts = attempts.filter(order__laundry__city__iexact=city)

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


def laundry_metrics(days=30):
    days = clamp_days(days)
    since = timezone.now() - timedelta(days=days)
    orders = Order.objects.filter(created_at__gte=since)

    top_by_orders = (
        orders.values('laundry_id', 'laundry__name')
        .annotate(n=Count('id')).order_by('-n')[:10]
    )
    top_by_revenue = (
        orders.filter(payment_status='PAID')
        .values('laundry_id', 'laundry__name')
        .annotate(total=Coalesce(Sum('total_amount'), Decimal('0'))).order_by('-total')[:10]
    )
    top_by_rating = (
        Review.objects.values('laundry_id', 'laundry__name')
        .annotate(avg=Avg('rating'), n=Count('id'))
        .filter(n__gte=1).order_by('-avg')[:10]
    )

    return {
        "window_days": days,
        "total_laundries": Laundry.objects.count(),
        "active_laundries": Laundry.objects.filter(is_active=True).count(),
        "top_by_orders": [
            {'name': r['laundry__name'] or 'Unknown', 'orders': r['n']} for r in top_by_orders
        ],
        "top_by_revenue": [
            {'name': r['laundry__name'] or 'Unknown', 'revenue': str(r['total'])}
            for r in top_by_revenue
        ],
        "top_by_rating": [
            {'name': r['laundry__name'] or 'Unknown', 'rating': round(r['avg'], 2), 'reviews': r['n']}
            for r in top_by_rating
        ],
    }


def retention_metrics(days=30):
    """Stickiness + N-day retention from AnalyticsEvent.

    Computed over users active in the window: distinct active days per user give
    returning-rate and stickiness; per-cohort D1/D7/D30 use each user's
    first-seen date. Bounded to active users (fine to ~1M events); move to a
    cohort rollup table beyond that.
    """
    from django.db.models import Min, Max

    days = clamp_days(days)
    now = timezone.now()
    since = now - timedelta(days=days)

    ev = AnalyticsEvent.objects.filter(created_at__gte=since, user__isnull=False)
    # Per-user first/last seen + distinct active days.
    per_user = (
        ev.values('user')
        .annotate(first=Min('created_at'), last=Max('created_at'),
                  active_days=Count(TruncDate('created_at'), distinct=True))
    )
    rows = list(per_user)
    active_users = len(rows)
    returning = sum(1 for r in rows if r['active_days'] >= 2)

    dau = ev.filter(created_at__gte=now - timedelta(days=1)).values('user').distinct().count()
    mau = ev.filter(created_at__gte=now - timedelta(days=30)).values('user').distinct().count()

    def n_day_retention(n):
        # Cohort: users first-seen at least n+1 days ago (so day-n is observable).
        cohort = [r for r in rows if (now - r['first']).days >= n + 1]
        if not cohort:
            return 0.0
        retained = sum(1 for r in cohort if (r['last'] - r['first']).days >= n)
        return rate(retained, len(cohort))

    return {
        "window_days": days,
        "active_users": active_users,
        "returning_users": returning,
        "returning_rate": rate(returning, active_users),
        "stickiness": rate(dau, mau),  # DAU/MAU
        "day_1_retention": n_day_retention(1),
        "day_7_retention": n_day_retention(7),
        "day_30_retention": n_day_retention(30),
    }


def _period_delta(current, previous):
    if not previous:
        return None
    return round(((current - previous) / previous) * 100, 1)


def ai_insights(days=7):
    """Rule-based insight cards: compare the current window to the prior one and
    surface notable deltas + a recommendation. Not an ML model — deterministic
    heuristics over the same aggregates the dashboards use."""
    days = clamp_days(days, default=7)
    now = timezone.now()
    cur_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=days * 2)

    def revenue_between(a, b):
        return Payment.objects.filter(status='SUCCESS', paid_at__gte=a, paid_at__lt=b)\
            .aggregate(t=Coalesce(Sum('amount'), Decimal('0')))['t']

    def orders_between(a, b):
        return Order.objects.filter(created_at__gte=a, created_at__lt=b).count()

    def new_users_between(a, b):
        return User.objects.filter(role='CUSTOMER', created_at__gte=a, created_at__lt=b).count()

    cur_rev, prev_rev = revenue_between(cur_start, now), revenue_between(prev_start, cur_start)
    cur_ord, prev_ord = orders_between(cur_start, now), orders_between(prev_start, cur_start)
    cur_users, prev_users = new_users_between(cur_start, now), new_users_between(prev_start, cur_start)

    insights = []

    def add(metric, cur, prev, good_up=True, fmt=str, recommend_down='', recommend_up=''):
        delta = _period_delta(float(cur), float(prev))
        if delta is None:
            return
        direction = 'up' if delta >= 0 else 'down'
        positive = (delta >= 0) == good_up
        text = f"{metric} {'increased' if delta >= 0 else 'decreased'} {abs(delta)}% vs the prior {days} days ({fmt(prev)} → {fmt(cur)})."
        rec = (recommend_up if delta >= 0 else recommend_down)
        insights.append({
            "metric": metric, "delta": delta, "direction": direction,
            "positive": positive, "text": text, "recommendation": rec,
        })

    add("Revenue", cur_rev, prev_rev, good_up=True,
        fmt=lambda v: f"GHS {Decimal(str(v)).quantize(Decimal('0.01'))}",
        recommend_down="Consider a rainy-day or reactivation campaign to lift revenue.",
        recommend_up="Momentum is positive — sustain with a referral push.")
    add("Orders", cur_ord, prev_ord, good_up=True,
        recommend_down="Order volume is dropping — run a promo or weekly reminder campaign.")
    add("New customers", cur_users, prev_users, good_up=True,
        recommend_down="Acquisition slowed — boost the referral bonus or marketing spend.")

    # Behavioural: highest-CTR campaign.
    top_campaign = (
        NotificationCampaign.objects.filter(delivered_count__gt=0)
        .order_by('-clicked_count').values('name', 'clicked_count', 'delivered_count').first()
    )
    if top_campaign:
        ctr = rate(top_campaign['clicked_count'], top_campaign['delivered_count'])
        insights.append({
            "metric": "Top campaign", "delta": None, "direction": "flat", "positive": True,
            "text": f"'{top_campaign['name']}' has the highest engagement at {ctr}% CTR.",
            "recommendation": "Reuse its copy/timing for the next broadcast.",
        })

    # Revenue forecast: naive linear projection from current run-rate.
    daily_rate = (cur_rev / days) if days else Decimal('0')
    forecast_30 = (daily_rate * 30).quantize(Decimal('0.01'))

    return {
        "window_days": days,
        "insights": insights,
        "revenue_forecast_30d": str(forecast_30),
    }


def realtime_feed(limit=15):
    now = timezone.now()
    active_window = now - timedelta(minutes=30)
    ev = AnalyticsEvent.objects.filter(created_at__gte=active_window)
    return {
        "active_sessions": ev.exclude(session_id='').values('session_id').distinct().count(),
        "active_users": ev.filter(user__isnull=False).values('user').distinct().count(),
        "events_last_30m": ev.count(),
        "by_platform_now": list(ev.values('platform').annotate(count=Count('id')).order_by('-count')),
        "recent_events": list(
            AnalyticsEvent.objects.order_by('-created_at')
            .values('event_name', 'platform', 'screen_name', 'created_at')[:limit]
        ),
        "recent_orders": list(
            Order.objects.order_by('-created_at')
            .values('order_no', 'status', 'total_amount', 'created_at')[:limit]
        ),
        "recent_payments": list(
            Payment.objects.order_by('-created_at')
            .values('transaction_reference', 'status', 'amount', 'created_at')[:limit]
        ),
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
