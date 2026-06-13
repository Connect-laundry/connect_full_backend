# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status, views, generics
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db.models import Count, Sum, Q, Avg
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from datetime import timedelta
# pyre-ignore[missing-module]
from ordering.models import Order, OrderItem
# pyre-ignore[missing-module]
from laundries.models.laundry import Laundry
# pyre-ignore[missing-module]
from laundries.models.service import LaundryService
# pyre-ignore[missing-module]
from ..serializers.dashboard import (
    DashboardOrderSerializer,
    DashboardStatsSerializer,
    DashboardEarningsSerializer,
    ServiceStatusUpdateSerializer
)
# pyre-ignore[missing-module]
from drf_spectacular.utils import extend_schema

class IsLaundryOwner(permissions.BasePermission):
    """
    Ensures the user owns the laundry associated with the dashboard request.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.role in ['OWNER', 'ADMIN']

class DashboardBaseView:
    def get_laundry(self, request):
        return Laundry.objects.filter(owner=request.user).first()

class DashboardOrderViewSet(viewsets.ReadOnlyModelViewSet, DashboardBaseView):
    """
    Paginated list of orders for the laundry owner.
    """
    queryset = Order.objects.none()
    serializer_class = DashboardOrderSerializer
    permission_classes = [IsLaundryOwner]

    def get_queryset(self):
        laundry = self.get_laundry(self.request)
        if not laundry:
            return Order.objects.none()
        return Order.objects.filter(laundry=laundry).select_related('user')

class DashboardStatsView(views.APIView, DashboardBaseView):
    """
    Aggregated order counts by status and advanced laundry metrics.
    """
    permission_classes = [IsLaundryOwner]

    @extend_schema(responses=DashboardStatsSerializer)
    def get(self, request):
        laundry = self.get_laundry(request)
        if not laundry:
            return Response({"error": "Laundry not found"}, status=404)

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        base_stats = Order.objects.filter(laundry=laundry).aggregate(
            pending_count=Count('id', filter=Q(status='PENDING')),
            confirmed_count=Count('id', filter=Q(status='CONFIRMED')),
            picked_up_count=Count('id', filter=Q(status='PICKED_UP')),
            delivered_count=Count('id', filter=Q(status='DELIVERED')),
            total_orders=Count('id')
        )

        # Revenue today, this month, AOV (Average Order Value)
        # Note: we use status__in=['DELIVERED', 'COMPLETED'] for realized revenue
        revenue_stats = Order.objects.filter(
            laundry=laundry,
            status__in=['DELIVERED', 'COMPLETED']
        ).aggregate(
            revenue_today=Sum('total_amount', filter=Q(created_at__gte=today_start), default=0.00),
            revenue_this_month=Sum('total_amount', filter=Q(created_at__gte=month_start), default=0.00),
            average_order_value=Avg('total_amount', default=0.00)
        )

        # Most popular items
        popular_items_qs = OrderItem.objects.filter(
            order__laundry=laundry
        ).values('name').annotate(
            quantity=Sum('quantity')
        ).order_by('-quantity')[:5]

        most_popular_items = [
            {"name": item['name'], "quantity": item['quantity']}
            for item in popular_items_qs
        ]

        # Repeat customer rate
        user_orders = Order.objects.filter(laundry=laundry).values('user').annotate(
            order_count=Count('id')
        )
        total_customers = len(user_orders)
        repeat_customers = sum(1 for c in user_orders if c['order_count'] >= 2)
        repeat_customer_rate = (repeat_customers / total_customers) * 100.0 if total_customers > 0 else 0.0

        # Pending pickups and pending deliveries count
        # Pending pickups = Confirmed orders waiting for pickup
        pending_pickups = Order.objects.filter(laundry=laundry, status='CONFIRMED').count()
        # Pending deliveries = In process or out for delivery
        pending_deliveries = Order.objects.filter(laundry=laundry, status__in=['IN_PROCESS', 'OUT_FOR_DELIVERY']).count()

        # Average turnaround time (hours)
        completed_orders = Order.objects.filter(
            laundry=laundry,
            status__in=['DELIVERED', 'COMPLETED'],
            completed_at__isnull=False
        ).only('created_at', 'completed_at')

        durations = [
            (o.completed_at - o.created_at).total_seconds() / 3600.0
            for o in completed_orders[:1000]
        ]
        average_turnaround_time = sum(durations) / len(durations) if durations else 0.0

        stats = {
            "pending_count": base_stats["pending_count"],
            "confirmed_count": base_stats["confirmed_count"],
            "picked_up_count": base_stats["picked_up_count"],
            "delivered_count": base_stats["delivered_count"],
            "total_orders": base_stats["total_orders"],
            "revenue_today": revenue_stats["revenue_today"],
            "revenue_this_month": revenue_stats["revenue_this_month"],
            "average_order_value": revenue_stats["average_order_value"],
            "most_popular_items": most_popular_items,
            "repeat_customer_rate": repeat_customer_rate,
            "pending_pickups": pending_pickups,
            "pending_deliveries": pending_deliveries,
            "average_turnaround_time": average_turnaround_time,
        }

        return Response({
            "status": "success",
            "data": stats
        })

class DashboardEarningsView(views.APIView, DashboardBaseView):
    """
    Revenue breakdown by period (today, week, month).
    """
    permission_classes = [IsLaundryOwner]

    @extend_schema(responses=DashboardEarningsSerializer)
    def get(self, request):
        laundry = self.get_laundry(request)
        if not laundry:
            return Response({"error": "Laundry not found"}, status=404)

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        earnings = Order.objects.filter(
            laundry=laundry, 
            status__in=['DELIVERED', 'COMPLETED']
        ).aggregate(
            today=Sum('total_amount', filter=Q(created_at__gte=today_start), default=0),
            this_week=Sum('total_amount', filter=Q(created_at__gte=week_start), default=0),
            this_month=Sum('total_amount', filter=Q(created_at__gte=month_start), default=0),
            total_revenue=Sum('total_amount', default=0)
        )

        return Response({
            "status": "success",
            "data": earnings
        })

class ServiceStatusUpdateView(generics.UpdateAPIView, DashboardBaseView):
    """
    Quickly toggle service availability from the dashboard.
    """
    serializer_class = ServiceStatusUpdateSerializer
    permission_classes = [IsLaundryOwner]
    lookup_field = 'id'

    def get_queryset(self):
        laundry = self.get_laundry(self.request)
        if not laundry:
            return LaundryService.objects.none()
        return LaundryService.objects.filter(laundry=laundry)
