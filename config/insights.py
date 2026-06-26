"""Connect Insights — Firebase-style multi-section analytics console.

A single staff-only view dispatches on `section`, building a normalized page
(`cards`, `charts`, `tables`, `notes`) from analytics.metrics (the same source
of truth as the DRF dashboard API and exporters). Each section is its own URL
so the console scales like an enterprise SaaS product rather than one giant page.
"""
import json
from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db import connection
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from analytics import metrics

# (key, label, icon_key) — drives the left navigation + routing.
# icon_key maps to SVG paths defined in the template; no emoji used.
SECTIONS = [
    ("overview",      "Overview",      "grid"),
    ("realtime",      "Realtime",      "activity"),
    ("users",         "Users",         "users"),
    ("orders",        "Orders",        "package"),
    ("revenue",       "Revenue",       "trending-up"),
    ("laundries",     "Laundries",     "droplets"),
    ("notifications", "Notifications", "bell"),
    ("marketing",     "Marketing",     "megaphone"),
    ("referrals",     "Referrals",     "share"),
    ("funnels",       "Funnels",       "filter"),
    ("retention",     "Retention",     "refresh-cw"),
    ("ai-insights",   "AI Insights",   "cpu"),
    ("errors",        "Errors",        "alert-triangle"),
    ("system-health", "System Health", "server"),
    ("reports",       "Reports",       "file-text"),
]
SECTION_KEYS = {s[0] for s in SECTIONS}


# ---- helpers ----------------------------------------------------------------

def _card(label, value, suffix='', hint=''):
    return {"label": label, "value": value, "suffix": suffix, "hint": hint}


def _chart(cid, title, kind, labels, values, label=''):
    return {"id": cid, "title": title, "kind": kind, "labels": labels,
            "values": values, "label": label}


def _series(rows, label_key, value_key):
    return ([r[label_key] for r in rows], [float(r[value_key]) for r in rows])


# ---- per-section builders ---------------------------------------------------

def _overview(days, city=None, laundry_id=None):
    ex = metrics.executive_metrics()
    rev = metrics.revenue_metrics(days, city=city, laundry_id=laundry_id)
    orders = metrics.order_metrics(days, city=city, laundry_id=laundry_id)
    users = metrics.user_metrics(days, city=city, laundry_id=laundry_id)
    rl, rv = _series(rev["revenue_by_day"], "day", "total")
    ol, ov = _series(orders["orders_by_day"], "day", "count")
    dl, dv = _series(users["daily_active_users"], "day", "users")
    return {
        "cards": [
            _card("Revenue today", ex["revenue_today"], " GHS"),
            _card("Revenue this month", ex["revenue_this_month"], " GHS"),
            _card("Orders today", ex["orders_today"]),
            _card("Active users (30m)", ex["active_users_now"]),
            _card("New users today", ex["new_users_today"]),
            _card("Pending orders", ex["pending_orders"]),
            _card("Avg order value", orders["average_order_value"], " GHS"),
            _card("Avg rating", ex["avg_rating"]),
        ],
        "charts": [
            _chart("ovRev", "Revenue per day", "line", rl, rv, "Revenue"),
            _chart("ovOrders", "Orders per day", "bar", ol, ov, "Orders"),
            _chart("ovDau", "Daily active users", "line", dl, dv, "DAU"),
        ],
    }


def _realtime(days, **kwargs):
    rt = metrics.realtime_feed()
    pl, pv = _series(rt["by_platform_now"], "platform", "count")
    return {
        "cards": [
            _card("Active sessions", rt["active_sessions"]),
            _card("Active users", rt["active_users"]),
            _card("Events (30m)", rt["events_last_30m"]),
        ],
        "charts": [_chart("rtPlat", "Active by platform", "doughnut", pl, pv)],
        "tables": [
            {"title": "Recent orders", "columns": ["Order", "Status", "Amount", "When"],
             "rows": [[r["order_no"], r["status"], str(r["total_amount"]),
                       r["created_at"].strftime("%H:%M:%S")] for r in rt["recent_orders"]]},
            {"title": "Recent payments", "columns": ["Ref", "Status", "Amount", "When"],
             "rows": [[r["transaction_reference"], r["status"], str(r["amount"]),
                       r["created_at"].strftime("%H:%M:%S")] for r in rt["recent_payments"]]},
            {"title": "Recent events", "columns": ["Event", "Platform", "Screen", "When"],
             "rows": [[r["event_name"], r["platform"], r["screen_name"],
                       r["created_at"].strftime("%H:%M:%S")] for r in rt["recent_events"]]},
        ],
        "notes": ["Auto-refreshes every 20s. Live geo-map deferred (needs map JS + coords)."],
    }


def _users(days, city=None, laundry_id=None):
    u = metrics.user_metrics(days, city=city)
    dl, dv = _series(u["daily_active_users"], "day", "users")
    nl, nv = _series(u["new_users_by_day"], "day", "count")
    pl, pv = _series(u["by_platform"], "platform", "count")
    return {
        "cards": [
            _card("DAU", u["dau"]), _card("WAU", u["wau"]), _card("MAU", u["mau"]),
            _card("New users", u["new_users"]), _card("Total customers", u["total_customers"]),
        ],
        "charts": [
            _chart("uDau", "Daily active users", "line", dl, dv, "DAU"),
            _chart("uNew", "New users per day", "bar", nl, nv, "New"),
            _chart("uPlat", "By platform", "doughnut", pl, pv),
        ],
    }


def _orders(days, city=None, laundry_id=None):
    o = metrics.order_metrics(days, city=city, laundry_id=laundry_id)
    ol, ov = _series(o["orders_by_day"], "day", "count")
    fl, fv = _series(o["funnel"], "stage", "count")
    return {
        "cards": [
            _card("Created", o["created"]), _card("Completed", o["completed"]),
            _card("Cancelled", o["cancelled"]), _card("In progress", o["in_progress"]),
            _card("Avg order value", o["average_order_value"], " GHS"),
            _card("Completion rate", o["completion_rate"], "%"),
            _card("Cancellation rate", o["cancellation_rate"], "%"),
        ],
        "charts": [
            _chart("oDay", "Orders per day", "bar", ol, ov, "Orders"),
            _chart("oFunnel", "Booking funnel", "bar", fl, fv, "Orders"),
        ],
    }


def _revenue(days, city=None, laundry_id=None):
    r = metrics.revenue_metrics(days, city=city, laundry_id=laundry_id)
    dl, dv = _series(r["revenue_by_day"], "day", "total")
    cl, cv = _series(r["revenue_by_city"], "city", "total")
    return {
        "cards": [
            _card("Gross revenue", r["gross_revenue"], " GHS"),
            _card("Platform revenue", r["platform_revenue"], " GHS"),
            _card("Net to laundries", r["net_to_laundries"], " GHS"),
            _card("Successful payments", r["successful_payments"]),
            _card("Failed payments", r["failed_payments"]),
            _card("Payment success rate", r["payment_success_rate"], "%"),
        ],
        "charts": [
            _chart("rDay", "Revenue per day", "line", dl, dv, "Revenue"),
            _chart("rCity", "Revenue by city", "bar", cl, cv, "Revenue"),
        ],
    }


def _laundries(days, city=None, laundry_id=None):
    m = metrics.laundry_metrics(days)
    return {
        "cards": [
            _card("Total laundries", m["total_laundries"]),
            _card("Active laundries", m["active_laundries"]),
        ],
        "tables": [
            {"title": "Top by orders", "columns": ["Laundry", "Orders"],
             "rows": [[r["name"], r["orders"]] for r in m["top_by_orders"]]},
            {"title": "Top by revenue", "columns": ["Laundry", "Revenue (GHS)"],
             "rows": [[r["name"], r["revenue"]] for r in m["top_by_revenue"]]},
            {"title": "Top rated", "columns": ["Laundry", "Rating", "Reviews"],
             "rows": [[r["name"], r["rating"], r["reviews"]] for r in m["top_by_rating"]]},
        ],
    }


def _notifications(days, **kwargs):
    n = metrics.notification_metrics()
    fl, fv = _series(n["funnel"], "stage", "count")
    return {
        "cards": [
            _card("Delivered", n["delivered"]), _card("Opened", n["opened"]),
            _card("Clicked", n["clicked"]), _card("Converted", n["converted"]),
            _card("Open rate", n["open_rate"], "%"), _card("Click rate", n["click_rate"], "%"),
            _card("Conversion rate", n["conversion_rate"], "%"),
            _card("Revenue", n["revenue"], " GHS"),
        ],
        "charts": [_chart("nFunnel", "Notification funnel", "bar", fl, fv, "Count")],
    }


def _marketing(days, **kwargs):
    from marketplace.models import NotificationCampaign
    n = metrics.notification_metrics()
    recent = (NotificationCampaign.objects.order_by('-created_at')
              .values('name', 'segment', 'status', 'recipients_count',
                      'delivered_count', 'clicked_count', 'converted_count',
                      'revenue_generated')[:15])
    return {
        "cards": [
            _card("Campaign revenue", n["revenue"], " GHS"),
            _card("Conversion rate", n["conversion_rate"], "%"),
            _card("Click rate", n["click_rate"], "%"),
        ],
        "tables": [{
            "title": "Recent campaigns",
            "columns": ["Name", "Segment", "Status", "Recipients", "Delivered", "Clicks", "Conv.", "Revenue"],
            "rows": [[c["name"], c["segment"], c["status"], c["recipients_count"],
                      c["delivered_count"], c["clicked_count"], c["converted_count"],
                      str(c["revenue_generated"])] for c in recent],
        }],
    }


def _referrals(days, **kwargs):
    r = metrics.referral_metrics()
    return {
        "cards": [
            _card("Referred users", r["total_referred_users"]),
            _card("Referrers", r["referrers"]),
            _card("Referral share of signups", r["referral_share_of_signups"], "%"),
        ],
        "tables": [{
            "title": "Top referrers", "columns": ["User", "Referrals"],
            "rows": [[r2["email"], r2["referrals"]] for r2 in r["top_referrers"]],
        }],
    }


def _funnels(days, **kwargs):
    o = metrics.order_metrics(days)
    n = metrics.notification_metrics()
    ol, ov = _series(o["funnel"], "stage", "count")
    nl, nv = _series(n["funnel"], "stage", "count")
    return {
        "charts": [
            _chart("fBooking", "Booking funnel (Created → Paid → Completed)", "bar", ol, ov, "Orders"),
            _chart("fNotif", "Notification funnel", "bar", nl, nv, "Count"),
        ],
        "notes": ["Screen-level funnels (Home → Browse → Details → Checkout) populate as the "
                  "app emits SCREEN_VIEW events through AnalyticsService."],
    }


def _retention(days, **kwargs):
    r = metrics.retention_metrics(days)
    return {
        "cards": [
            _card("Day 1 retention", r["day_1_retention"], "%"),
            _card("Day 7 retention", r["day_7_retention"], "%"),
            _card("Day 30 retention", r["day_30_retention"], "%"),
            _card("Stickiness (DAU/MAU)", r["stickiness"], "%"),
            _card("Returning users", r["returning_users"]),
            _card("Returning rate", r["returning_rate"], "%"),
        ],
        "charts": [_chart("retCurve", "Retention by day", "bar",
                          ["Day 1", "Day 7", "Day 30"],
                          [r["day_1_retention"], r["day_7_retention"], r["day_30_retention"]],
                          "Retention %")],
    }


def _ai_insights(days, **kwargs):
    ai = metrics.ai_insights(days)
    return {
        "cards": [_card("Revenue forecast (30d)", ai["revenue_forecast_30d"], " GHS",
                        "Naive run-rate projection")],
        "insights": ai["insights"],
        "notes": ["Heuristic insights (deterministic period-comparison), not an ML model. "
                  "A trained forecast/anomaly model is a future enhancement."],
    }


def _errors(days, **kwargs):
    from analytics import sentry_service

    summary = sentry_service.get_summary()
    if not summary["configured"]:
        return {
            "cards": [_card("Sentry", "Not configured")],
            "notes": [
                "Live error analytics need a Sentry org auth token. Set SENTRY_API_TOKEN, "
                "SENTRY_ORG_SLUG and SENTRY_PROJECT_SLUG (token scope: project:read) to "
                "populate the open-issue list, affected users and event counts here.",
            ],
        }

    issues = sentry_service.get_issues(limit=15)["issues"]
    notes = []
    if summary["error"]:
        notes.append(summary["error"])
    notes.append("Crash-free session % requires the Sentry Sessions API; the issue list, "
                 "event counts and affected users above come from the Issues API (last 24h).")
    return {
        "cards": [
            _card("Open issues (24h)", summary["open_issues"]),
            _card("Events (24h)", summary["events_24h"]),
            _card("Affected users", summary["affected_users"]),
        ],
        "tables": [{
            "title": "Top unresolved issues (last 24h)",
            "columns": ["Issue", "Level", "Events", "Users", "Last seen"],
            "rows": [[i["title"], i["level"], i["count"], i["user_count"],
                      (i["last_seen"] or "")[:19].replace("T", " ")] for i in issues],
        }],
        "notes": notes,
    }


def _system_health(days, **kwargs):
    cards = []

    # Database.
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        cards.append(_card("Database", "Healthy"))
    except Exception:
        cards.append(_card("Database", "Critical"))

    # Cache / Redis.
    try:
        cache.set("insights_health_probe", "1", 10)
        ok = cache.get("insights_health_probe") == "1"
        cards.append(_card("Cache / Redis", "Healthy" if ok else "Warning"))
    except Exception:
        cards.append(_card("Cache / Redis", "Critical"))

    # Configured integrations (presence of credentials, not live pings).
    import os
    from django.conf import settings as s

    def cfg(name, *envs, attr=None):
        present = bool(getattr(s, attr, '') if attr else '') or any(os.getenv(e) for e in envs)
        cards.append(_card(name, "Configured" if present else "Not configured"))

    cfg("Paystack", "PAYSTACK_SECRET_KEY", attr="PAYSTACK_SECRET_KEY")
    cfg("Expo Push", "EXPO_PUSH_ENABLED", attr="EXPO_PUSH_ENABLED")
    cfg("Cloudinary", "CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME")
    cfg("Clerk", "CLERK_SECRET_KEY", "CLERK_APPLICATION_ID", attr="CLERK_APPLICATION_ID")
    cfg("Sentry", "SENTRY_DSN", attr="SENTRY_DSN")
    cfg("Google Maps", "GOOGLE_MAPS_API_KEY", "EXPO_PUBLIC_GOOGLE_MAPS_API_KEY")
    cfg("Weather", "WEATHER_PROMO_ENABLED")

    return {
        "cards": cards,
        "notes": ["Status reflects reachability (DB/cache) and credential presence "
                  "(integrations). Host CPU/memory/disk + Celery worker liveness need a "
                  "metrics exporter (e.g. Render metrics / Flower) — wire those probes here."],
    }


def _reports(days, **kwargs):
    return {
        "reports": True,
        "days": days,
        "notes": ["Generate on-demand exports below. Scheduled email reports run via a "
                  "Celery beat task (add a recipient list + schedule to enable)."],
    }


BUILDERS = {
    "overview": _overview, "realtime": _realtime, "users": _users, "orders": _orders,
    "revenue": _revenue, "laundries": _laundries, "notifications": _notifications,
    "marketing": _marketing, "referrals": _referrals, "funnels": _funnels,
    "retention": _retention, "ai-insights": _ai_insights, "errors": _errors,
    "system-health": _system_health, "reports": _reports,
}


# Sections where the city/laundry filter selectors are meaningful.
FILTERABLE = {"overview", "orders", "revenue", "users", "laundries"}


def _filter_options():
    """Distinct cities + laundries for the selector dropdowns (cached briefly)."""
    from laundries.models.laundry import Laundry
    opts = cache.get('insights:filter_options')
    if opts is None:
        cities = sorted(
            c for c in Laundry.objects.values_list('city', flat=True).distinct() if c
        )
        laundries = list(
            Laundry.objects.order_by('name').values('id', 'name')[:500]
        )
        opts = {'cities': cities,
                'laundries': [{'id': str(l['id']), 'name': l['name']} for l in laundries]}
        cache.set('insights:filter_options', opts, 300)
    return opts


@staff_member_required
def insights_view(request, section="overview"):
    if section not in SECTION_KEYS:
        raise Http404("Unknown insights section")
    try:
        days = min(int(request.GET.get("days", 30)), 365)
    except (TypeError, ValueError):
        days = 30

    city = (request.GET.get("city") or "").strip() or None
    laundry_id = (request.GET.get("laundry_id") or "").strip() or None

    page = BUILDERS[section](days, city=city, laundry_id=laundry_id)

    options = _filter_options()
    context = {
        **admin.site.each_context(request),
        "title": "Connect Insights",
        "section_key": section,
        "section_label": dict((k, l) for k, l, _ in SECTIONS)[section],
        "sections": [{"key": k, "label": l, "icon_key": i} for k, l, i in SECTIONS],
        "days": days,
        "day_options": [1, 7, 30, 90],
        "show_filters": section in FILTERABLE,
        "cities": options["cities"],
        "laundries": options["laundries"],
        "selected_city": city or "",
        "selected_laundry": laundry_id or "",
        "cards": page.get("cards", []),
        "tables": page.get("tables", []),
        "insights": page.get("insights", []),
        "notes": page.get("notes", []),
        "is_reports": page.get("reports", False),
        "charts_json": json.dumps(page.get("charts", [])),
    }
    return render(request, "admin/insights/page.html", context)
