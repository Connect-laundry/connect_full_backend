# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status
# pyre-ignore[missing-module]
from rest_framework.decorators import action
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.db.models import Avg, Count, F, ExpressionWrapper, FloatField, Q
# pyre-ignore[missing-module]
from django.db.models.functions import Sqrt, Sin, Cos, ASin, Radians
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django_filters.rest_framework import DjangoFilterBackend
import logging

logger = logging.getLogger(__name__)
# pyre-ignore[missing-module]
from rest_framework.filters import SearchFilter
# pyre-ignore[missing-module]
from rest_framework.renderers import JSONRenderer
# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.favorite import Favorite
# pyre-ignore[missing-module]
from ..models.opening_hours import OpeningHours
# pyre-ignore[missing-module]
from ..models.review import Review
# pyre-ignore[missing-module]
from ..serializers.laundry_list import LaundryListSerializer
# pyre-ignore[missing-module]
from ..serializers.laundry_detail import LaundryDetailSerializer
# pyre-ignore[missing-module]
from ..serializers.review import ReviewSerializer
# pyre-ignore[missing-module]
from ..pagination import StandardResultsSetPagination
# pyre-ignore[missing-module]
from ..filters import LaundryFilter
# pyre-ignore[missing-module]
from config.throttling import ReviewThrottle


class LaundryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Laundry.objects.filter(
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True
    ).select_related('owner').annotate(
        rating=Avg('reviews__rating'),
        reviewsCount=Count('reviews'),
        active_order_count=Count(
            'orders',
            filter=models.Q(orders__status__in=['PENDING', 'PICKED_UP', 'WASHING'])
        )
    )
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = LaundryFilter
    search_fields = ['name', 'description', 'address']
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Prefetch reviews and services for detail view to avoid N+1
        if self.action == 'retrieve':
            queryset = queryset.prefetch_related(
                'services__category',
                'reviews__user',
                'opening_hours'
            )
        
        # Nearby Search Logic
        nearby = self.request.query_params.get('nearby') == 'true'
        lat = self.request.query_params.get('lat')
        lng = self.request.query_params.get('lng')

        if nearby and lat and lng:
            try:
                lat = float(lat)
                lng = float(lng)
                
                # Haversine formula in Django ORM
                dlat = Radians(F('latitude') - lat)
                dlng = Radians(F('longitude') - lng)
                
                a = Sin(dlat / 2)**2 + Cos(Radians(lat)) * Cos(Radians(F('latitude'))) * Sin(dlng / 2)**2
                c = 2 * ASin(Sqrt(a))
                distance_km = ExpressionWrapper(c * 6371, output_field=FloatField())
                
                queryset = queryset.annotate(distance=distance_km).filter(distance__lte=10).order_by('distance')
            except (ValueError, TypeError):
                pass
                
        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return LaundryListSerializer
        return LaundryDetailSerializer

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def favorite(self, request, pk=None):
        laundry = self.get_object()
        favorite, created = Favorite.objects.get_or_create(user=request.user, laundry=laundry)
        
        if not created:
            favorite.delete()
            return Response({
                "status": "success",
                "message": "Removed from favorites."
            }, status=status.HTTP_200_OK)
            
        return Response({
            "status": "success",
            "message": "Added to favorites."
        }, status=status.HTTP_201_CREATED)
    @action(detail=True, methods=['patch'], permission_classes=[permissions.IsAuthenticated])
    def deactivate(self, request, pk=None):
        laundry = self.get_object()
        
        # Check permissions: Admin or Owner
        if not request.user.is_staff and laundry.owner != request.user:
            return Response(
                {"status": "error", "message": "You do not have permission to deactivate this laundry."},
                status=status.HTTP_403_FORBIDDEN
            )
            
        reason = request.data.get('reason', 'No reason provided')
        
        if not laundry.is_active:
            return Response(
                {"status": "error", "message": "Laundry is already inactive"},
                status=status.HTTP_400_BAD_REQUEST
            )

        laundry.is_active = False
        laundry.deactivated_at = timezone.now()
        laundry.deactivation_reason = reason
        laundry.save()

        logger.info(f"Laundry {laundry.id} deactivated by {request.user.email}. Reason: {reason}")
        
        return Response({
            "status": "success",
            "message": f"Laundry {laundry.name} has been deactivated.",
            "data": {
                "id": laundry.id,
                "is_active": laundry.is_active,
                "deactivated_at": laundry.deactivated_at,
                "reason": laundry.deactivation_reason
            }
        })
