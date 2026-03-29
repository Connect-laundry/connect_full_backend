# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status, views, generics
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db.models import Count, Sum, Q
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from datetime import timedelta
# pyre-ignore[missing-module]
from ordering.models import Order
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
    serializer_class = DashboardOrderSerializer
    permission_classes = [IsLaundryOwner]

    def get_queryset(self):
        laundry = self.get_laundry(self.request)
        if not laundry:
            return Order.objects.none()
        return Order.objects.filter(laundry=laundry).select_related('user')

# pyre-ignore[missing-module]
from laundries.models.review import Review

class DashboardStatsView(views.APIView, DashboardBaseView):
    """
    Aggregated order counts by status + recent activity.
    """
    permission_classes = [IsLaundryOwner]

    def get(self, request):
        laundry = self.get_laundry(request)
        if not laundry:
            return Response({"error": "Laundry not found"}, status=404)

        stats = Order.objects.filter(laundry=laundry).aggregate(
            pending_count=Count('id', filter=Q(status='PENDING')),
            confirmed_count=Count('id', filter=Q(status='CONFIRMED')),
            picked_up_count=Count('id', filter=Q(status='PICKED_UP')),
            delivered_count=Count('id', filter=Q(status='DELIVERED')),
            total_orders=Count('id')
        )
        
        # Add recent activity
        stats['recent_orders'] = Order.objects.filter(laundry=laundry).select_related('user').order_by('-created_at')[:5]
        stats['recent_reviews'] = Review.objects.filter(laundry=laundry).select_related('user').order_by('-created_at')[:5]

        serializer = DashboardStatsSerializer(stats)
        
        return Response({
            "success": True,
            "data": serializer.data
        })

class DashboardEarningsView(views.APIView, DashboardBaseView):
    """
    Revenue breakdown by period (today, week, month) + time-series data + sentiment.
    """
    permission_classes = [IsLaundryOwner]

    def get(self, request):
        laundry = self.get_laundry(request)
        if not laundry:
            return Response({"error": "Laundry not found"}, status=404)

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=now.weekday())
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        completed_orders = Order.objects.filter(
            laundry=laundry, 
            status__in=['DELIVERED', 'COMPLETED']
        )

        earnings = completed_orders.aggregate(
            today=Sum('final_price', filter=Q(created_at__gte=today_start), default=0),
            this_week=Sum('final_price', filter=Q(created_at__gte=week_start), default=0),
            this_month=Sum('final_price', filter=Q(created_at__gte=month_start), default=0),
            total_revenue=Sum('final_price', default=0)
        )

        # ─── Time-Series Revenue (Last 12 days) ─────────────────
        from django.db.models.functions import TruncDate
        time_series = list(
            completed_orders
            .filter(created_at__gte=now - timedelta(days=12))
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(revenue=Sum('final_price'))
            .order_by('date')
        )
        # Stringify dates for JSON
        for entry in time_series:
            entry['date'] = entry['date'].isoformat() if entry['date'] else None

        # ─── Sentiment Score ─────────────────────────────────────
        from laundries.models.review import Review
        total_reviews = Review.objects.filter(laundry=laundry).count()
        positive_reviews = Review.objects.filter(laundry=laundry, rating__gte=4).count()
        sentiment_score = round((positive_reviews / total_reviews) * 100, 1) if total_reviews > 0 else None

        return Response({
            "success": True,
            "data": {
                **earnings,
                "time_series": time_series,
                "sentiment": {
                    "total_reviews": total_reviews,
                    "positive_reviews": positive_reviews,
                    "score": sentiment_score,
                }
            }
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
