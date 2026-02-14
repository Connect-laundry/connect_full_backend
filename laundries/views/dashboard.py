from rest_framework import viewsets, permissions, status, views, generics
from rest_framework.response import Response
from django.db.models import Count, Sum, Q
from django.utils import timezone
from datetime import timedelta
from ordering.models import Order
from laundries.models.laundry import Laundry
from laundries.models.service import Service
from .dashboard_serializers import (
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

class DashboardStatsView(views.APIView, DashboardBaseView):
    """
    Aggregated order counts by status.
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
        
        return Response({
            "status": "success",
            "data": stats
        })

class DashboardEarningsView(views.APIView, DashboardBaseView):
    """
    Revenue breakdown by period (today, week, month).
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
            return Service.objects.none()
        return Service.objects.filter(laundry=laundry)
