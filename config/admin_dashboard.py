from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from datetime import timedelta

def dashboard_callback(request, context):
    """
    Callback function to provide metrics for the Unfold dashboard.
    """
    from users.models import User
    from ordering.models.base import Order
    from payments.models import Payment

    # Operational Metrics
    total_users = User.objects.count()
    verified_users = User.objects.filter(is_verified=True).count()
    
    active_orders = Order.objects.exclude(
        status__in=['completed', 'cancelled', 'rejected']
    ).count()
    
    pending_pickups = Order.objects.filter(status='pending').count()
    
    # Financial Metrics
    total_revenue = Payment.objects.filter(status='SUCCESS').aggregate(
        total=Sum('amount'))['total'] or 0
        
    # Recent Activity (Last 24h)
    last_24h = timezone.now() - timedelta(hours=24)
    new_orders_24h = Order.objects.filter(created_at__gte=last_24h).count()
    
    context.update({
        "kpi_metrics": [
            {
                "title": _("Total Revenue"),
                "metric": f"{total_revenue:,.2f} GHS",
                "footer": _("Successful transactions"),
                "icon": "payments",
            },
            {
                "title": _("Active Orders"),
                "metric": active_orders,
                "footer": f"{new_orders_24h} " + _("in last 24h"),
                "icon": "shopping_cart",
            },
            {
                "title": _("Verified Users"),
                "metric": verified_users,
                "footer": f"{total_users} " + _("total accounts"),
                "icon": "people",
            },
            {
                "title": _("Pending Pickups"),
                "metric": pending_pickups,
                "footer": _("Awaiting operator action"),
                "icon": "schedule",
            },
        ]
    })
    return context
