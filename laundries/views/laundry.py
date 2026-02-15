# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status
# pyre-ignore[missing-module]
from rest_framework.decorators import action
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.db.models import Avg, Count, F, ExpressionWrapper, FloatField, Q, Prefetch
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter
import logging
import os

# Check if PostGIS is enabled
USE_POSTGIS = os.getenv('USE_POSTGIS', 'False') == 'True'

# Conditionally import GIS modules
if USE_POSTGIS:
    # pyre-ignore[missing-module]
    from django.contrib.gis.db.models.functions import Distance
    # pyre-ignore[missing-module]
    from django.contrib.gis.geos import Point
    # pyre-ignore[missing-module]
    from django.contrib.gis.measure import D
else:
    # Mock GIS classes for non-PostGIS mode
    Distance = None
    Point = None
    D = None

# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.service import Service
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
from ..pagination import StandardResultsSetPagination
# pyre-ignore[missing-module]
from ..filters import LaundryFilter

logger = logging.getLogger(__name__)

class LaundryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for exploring laundries. Optimized with PostGIS for proximity search.
    """
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
                Prefetch(
                    'services',
                    queryset=Service.objects.filter(is_active=True, is_approved=True).select_related('category')
                ),
                'reviews__user',
                'opening_hours'
            )
        
        # Optimized Spatial Nearby Search Logic (only if PostGIS is enabled)
        if not USE_POSTGIS:
            # Skip spatial queries if PostGIS is not enabled
            return queryset
            
        nearby = self.request.query_params.get('nearby') == 'true'
        lat = self.request.query_params.get('lat')
        lng = self.request.query_params.get('lng')
        radius_km = float(self.request.query_params.get('radius', 10)) # Default 10km

        if nearby and lat and lng:
            try:
                user_location = Point(float(lng), float(lat), srid=4326)
                
                # 1. Spatial filter using PostGIS index (ST_DWithin)
                queryset = queryset.filter(location__dwithin=(user_location, D(km=radius_km)))
                
                # 2. Annotate exact distance for display (ST_Distance)
                queryset = queryset.annotate(distance=Distance('location', user_location)).order_by('distance')
                
                logger.info(f"Spatial search triggered for ({lat}, {lng}) within {radius_km}km")
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid coordinates for nearby search: {e}")
                
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
