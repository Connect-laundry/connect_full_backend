# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status, decorators
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db.models import Count, Sum, Avg, Q, F
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django.core.cache import cache
# pyre-ignore[missing-module]
from django.conf import settings
from decimal import Decimal
from datetime import timedelta
import logging

# pyre-ignore[missing-module]
from ordering.models import Order
# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.service import Service
# pyre-ignore[missing-module]
from ..serializers.dashboard import (
    DashboardOrderSerializer, 
    BulkServiceUpdateSerializer,
    ServicePriceUpdateSerializer
)

logger = logging.getLogger(__name__)

class LaundryDashboardViewSet(viewsets.GenericViewSet):
    """
    Business side operational control APIs for Laundry Owners.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_laundry(self):
        # laundry owner must own the laundry
        laundry = Laundry.objects.filter(owner=self.request.user).first()
        if not laundry:
            return None
        return laundry

    @decorators.action(detail=False, methods=['get'])
    def orders(self, request):
        """Fetch all orders for the authenticated laundry with filtering."""
        laundry = self.get_laundry()
        if not laundry:
            return Response({"error": "No laundry found for this user."}, status=status.HTTP_404_NOT_FOUND)

        queryset = Order.objects.filter(laundry=laundry).select_related('user')

        # status filtering
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # date filtering
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = DashboardOrderSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = DashboardOrderSerializer(queryset, many=True)
        return Response({
            "status": "success",
            "message": "Laundry orders fetched",
            "data": serializer.data
        })

    @decorators.action(detail=False, methods=['get'])
    def stats(self, request):
        """Fetch operational metrics with 60s Redis caching."""
        laundry = self.get_laundry()
        if not laundry:
            return Response({"error": "No laundry found."}, status=status.HTTP_404_NOT_FOUND)

        cache_key = f"laundry_stats_{laundry.id}"
        cached_stats = cache.get(cache_key)
        if cached_stats:
            return Response({
                "status": "success",
                "message": "Dashboard stats fetched (cached)",
                "data": cached_stats
            })

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Aggregation query
        stats = Order.objects.filter(laundry=laundry).aggregate(
            total_orders=Count('id'),
            pending_orders=Count('id', filter=Q(status='PENDING')),
            in_progress_orders=Count('id', filter=Q(status='IN_PROCESS')),
            completed_today=Count('id', filter=Q(status='COMPLETED', completed_at__gte=today_start)),
            cancelled_today=Count('id', filter=Q(status='CANCELLED', cancelled_at__gte=today_start)),
            # average_processing_time: duration between processing_started_at and completed_at
            # This requires F expression or specialized query
        )

        # Rating average from internal review model if exists
        stats['rating_average'] = laundry.reviews.aggregate(Avg('rating'))['rating__avg'] or 0.0
        
        # Calculate real average processing time using DB aggregation
        from django.db.models import ExpressionWrapper, DurationField
        avg_processing = Order.objects.filter(
            laundry=laundry, 
            status='COMPLETED', 
            processing_started_at__isnull=False, 
            completed_at__isnull=False
        ).aggregate(
            avg_time=Avg(ExpressionWrapper(F('completed_at') - F('processing_started_at'), output_field=DurationField()))
        )['avg_time']
        
        stats['average_processing_time_minutes'] = int(avg_processing.total_seconds() / 60) if avg_processing else 0

        cache.set(cache_key, stats, 60)

        return Response({
            "status": "success",
            "message": "Dashboard stats fetched",
            "data": stats
        })

    @decorators.action(detail=False, methods=['get'])
    def earnings(self, request):
        """Revenue analytics for COMPLETED orders."""
        laundry = self.get_laundry()
        if not laundry:
            return Response({"error": "No laundry found."}, status=status.HTTP_404_NOT_FOUND)

        queryset = Order.objects.filter(laundry=laundry, status='COMPLETED')

        period = request.query_params.get('period', 'daily')
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')

        if date_from:
            queryset = queryset.filter(completed_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(completed_at__lte=date_to)

        agg = queryset.aggregate(
            total_revenue=Sum('total_amount'),
            completed_orders=Count('id')
        )

        total_revenue = agg['total_revenue'] or Decimal('0.00')
        commission_rate = Decimal(getattr(settings, 'PLATFORM_COMMISSION_RATE', '0.10'))
        commission = total_revenue * commission_rate
        net = total_revenue - commission

        # Simple chart aggregation (daily)
        chart_data = queryset.extra(select={'day': "date(completed_at)"}).values('day').annotate(revenue=Sum('total_amount')).order_by('day')

        return Response({
            "status": "success",
            "message": "Earnings fetched",
            "data": {
                "total_revenue": total_revenue,
                "completed_orders": agg['completed_orders'],
                "platform_commission": commission,
                "net_earnings": net,
                "chart": chart_data
            }
        })

    @decorators.action(detail=False, methods=['patch'], url_path='services')
    def update_services(self, request):
        """Batch update service pricing and availability."""
        laundry = self.get_laundry()
        if not laundry:
            return Response({"error": "No laundry found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = BulkServiceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated_services = []
        with timezone.now() as now: # using context for atomic if needed, but we use transaction.atomic
            # pyre-ignore[missing-module]
            from django.db import transaction
            with transaction.atomic():
                for service_data in serializer.validated_data['services']:
                    service_id = service_data['id']
                    # Ensure laundry owns the service
                    service = Service.objects.filter(id=service_id, laundry=laundry).first()
                    if service:
                        service.base_price = service_data['base_price']
                        service.is_active = service_data['is_active']
                        service.save()
                        updated_services.append(service.id)
                        logger.info(f"Service {service_id} updated by owner {request.user.email}")

        return Response({
            "status": "success",
            "message": f"Updated {len(updated_services)} services",
            "data": {"updated_ids": updated_services}
        })
