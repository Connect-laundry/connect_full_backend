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
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from django_filters.rest_framework import DjangoFilterBackend
# pyre-ignore[missing-module]
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
    from django.db import connection
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
from ..models.service import LaundryService
# pyre-ignore[missing-module]
from ..models.favorite import Favorite
# pyre-ignore[missing-module]
from ..models.category import Category
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
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = LaundryFilter
    search_fields = ['name', 'description', 'address']
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        # Prevent premature database hits during schema generation or discovery
        if getattr(self, 'swagger_fake_view', False):
            return Laundry.objects.none()

        # 1. Base queryset with essential annotations
        queryset = Laundry.objects.filter(
            status=Laundry.ApprovalStatus.APPROVED,
            is_active=True
        ).select_related('owner').annotate(
            rating=Avg('reviews__rating'),
            reviewsCount=Count('reviews', distinct=True),
            active_order_count=Count(
                'orders',
                filter=models.Q(orders__status__in=['PENDING', 'PICKED_UP', 'IN_PROCESS', 'OUT_FOR_DELIVERY']),
                distinct=True
            )
        ).order_by('-created_at')

        # 2. Prefetch reviews and services for detail view to avoid N+1
        if self.action == 'retrieve' or self.action == 'list' or self.action == 'featured':
            # Production logic: Owners and Admins can see "Pending" services as drafts.
            # Customers only see "Approved" services.
            user = self.request.user
            laundry_id = self.kwargs.get('pk')
            
            # Simple permission check for the prefetch filter
            show_all_services = False
            if user.is_authenticated:
                if user.is_staff:
                    show_all_services = True
                elif laundry_id:
                    # Check if user owns the laundry being retrieved
                    laundry_owner_exists = Laundry.objects.filter(id=laundry_id, owner=user).exists()
                    show_all_services = laundry_owner_exists

            service_filter = Q(is_active=True)
            if not show_all_services:
                # Note: LaundryService model uses 'is_available', not 'is_approved'
                service_filter &= Q(is_available=True)

            prefetch_items = [
                'opening_hours',
            ]

            if self.action == 'retrieve':
                prefetch_items.extend([
                    Prefetch(
                        'laundry_services',
                        queryset=LaundryService.objects.filter(is_available=True).select_related('service_type', 'item')
                    ),
                    'reviews__user',
                ])

            queryset = queryset.prefetch_related(*prefetch_items)
        
        # 3. Optimized Spatial Nearby Search Logic (only if PostGIS is enabled)
        if USE_POSTGIS:
            nearby = self.request.query_params.get('nearby') == 'true'
            lat = self.request.query_params.get('lat') or self.request.query_params.get('latitude')
            lng = self.request.query_params.get('lng') or self.request.query_params.get('longitude')
            radius_km = 10
            try:
                radius_param = self.request.query_params.get('radius')
                if radius_param:
                    radius_km = float(radius_param)
            except (ValueError, TypeError):
                pass

            if nearby and lat and lng:
                try:
                    # pyre-ignore[reportAttributeAccessIssue]
                    user_location = Point(float(lng), float(lat), srid=4326)
                    
                    # Spatial filter using PostGIS distance lookup (standard for Geography/Geometry)
                    # pyre-ignore[reportAttributeAccessIssue]
                    queryset = queryset.filter(location__distance_lte=(user_location, D(km=radius_km)))
                    
                    # Annotate exact distance for display (ST_Distance)
                    # pyre-ignore[reportAttributeAccessIssue]
                    queryset = queryset.annotate(distance=Distance('location', user_location)).order_by('distance')
                    
                    logger.info(f"Spatial search triggered for ({lat}, {lng}) within {radius_km}km")
                except (ValueError, TypeError, Exception) as e:
                    logger.error(f"Error in nearby search: {e}", exc_info=True)

        # 4. Recommended Sorting Logic
        recommended = self.request.query_params.get('recommended') == 'true'
        if recommended:
            # pyre-ignore[missing-module]
            from django.db.models.functions import Coalesce
            queryset = queryset.annotate(
                safe_rating=Coalesce('rating', 0.0, output_field=FloatField()),
                score=ExpressionWrapper(F('safe_rating') * F('reviewsCount'), output_field=FloatField())
            ).order_by('-score', '-safe_rating')

        # 5. Cheapest Sorting Logic
        cheapest = self.request.query_params.get('cheapest') == 'true'
        if cheapest:
            queryset = queryset.annotate(
                avg_price=Avg('laundry_services__price')
            ).order_by(F('avg_price').asc(nulls_last=True))
        
        # 6. Featured Filter
        if self.request.query_params.get('is_featured') == 'true' or self.request.query_params.get('featured') == 'true':
            queryset = queryset.filter(is_featured=True)
                
        return queryset

    def get_serializer_class(self):
        if self.action == 'list' or self.action == 'featured':
            return LaundryListSerializer
        return LaundryDetailSerializer

    @action(detail=False, methods=['get'])
    def featured(self, request):
        """
        Dedicated endpoint for featured laundries.
        """
        try:
            queryset = self.get_queryset().filter(is_featured=True)
            
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response({
                "success": True,
                "message": "Featured laundries retrieved successfully.",
                "data": serializer.data
            })
        except Exception as e:
            logger.error(f"Critical error in featured laundries endpoint: {e}", exc_info=True)
            return Response({
                "success": False,
                "message": "An error occurred while fetching featured laundries.",
                "data": {}
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def favorite(self, request, pk=None):
        laundry = self.get_object()
        favorite, created = Favorite.objects.get_or_create(user=request.user, laundry=laundry)
        
        if not created:
            favorite.delete()
            return Response({
                "success": True,
                "message": "Removed from favorites."
            }, status=status.HTTP_200_OK)
            
        return Response({
            "success": True,
            "message": "Added to favorites."
        }, status=status.HTTP_201_CREATED)
    @action(detail=True, methods=['patch'], permission_classes=[permissions.IsAuthenticated])
    def deactivate(self, request, pk=None):
        laundry = self.get_object()
        
        # Check permissions: Admin or Owner
        if not request.user.is_staff and laundry.owner != request.user:
            return Response(
                {"success": False, "status": "error", "message": "You do not have permission to deactivate this laundry."},
                status=status.HTTP_403_FORBIDDEN
            )
            
        reason = request.data.get('reason', 'No reason provided')
        
        if not laundry.is_active:
            return Response(
                {"success": False, "status": "error", "message": "Laundry is already inactive"},
                status=status.HTTP_400_BAD_REQUEST
            )

        laundry.is_active = False
        laundry.deactivated_at = timezone.now()
        laundry.deactivation_reason = reason
        laundry.save()

        logger.info(f"Laundry {laundry.id} deactivated by {request.user.email}. Reason: {reason}")
        
        return Response({
            "success": True,
            "message": f"Laundry {laundry.name} has been deactivated.",
            "data": {
                "id": laundry.id,
                "is_active": laundry.is_active,
                "deactivated_at": laundry.deactivated_at,
                "reason": laundry.deactivation_reason
            }
        })

    @action(detail=True, methods=['get', 'post'], permission_classes=[permissions.IsAuthenticatedOrReadOnly])
    def services(self, request, pk=None):
        """
        GET: Returns all configured LaundryServices (pricing and availability) for this laundry.
        POST: Upserts pricing for a specific item and service type (Owner/Admin only).
        """
        laundry = self.get_object()
        
        # pyre-ignore[missing-module]
        from ..models.service import LaundryService
        # pyre-ignore[missing-module]
        from ..serializers.laundry_detail import LaundryServiceSerializer

        if request.method == 'GET':
            # Optionally filter by is_available for non-owners
            qs = laundry.laundry_services.select_related('item', 'service_type').all()
            
            if not request.user.is_staff and laundry.owner != request.user:
                qs = qs.filter(is_available=True)
                
            serializer = LaundryServiceSerializer(qs, many=True, context={'request': request})
            return Response({
                "success": True,
                "message": "Laundry services retrieved successfully.",
                "data": serializer.data
            })
            
        elif request.method == 'POST':
            # Check Owner/Admin Permission
            if not request.user.is_staff and laundry.owner != request.user:
                return Response(
                    {"success": False, "status": "error", "message": "You do not have permission to manage services for this laundry."},
                    status=status.HTTP_403_FORBIDDEN
                )
                
            # Upsert logic
            item_id = request.data.get('item_id')
            service_type_id = request.data.get('service_type_id')
            price = request.data.get('price')
            estimated_duration = request.data.get('estimated_duration', '')
            is_available = request.data.get('is_available', True)
            
            if not all([item_id, service_type_id, price]):
                return Response(
                    {"success": False, "status": "error", "message": "item_id, service_type_id, and price are required."},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            laundry_service, created = LaundryService.objects.update_or_create(
                laundry=laundry,
                item_id=item_id,
                service_type_id=service_type_id,
                defaults={
                    'price': price,
                    'estimated_duration': estimated_duration,
                    'is_available': str(is_available).lower() == 'true'
                }
            )
            
            serializer = LaundryServiceSerializer(laundry_service, context={'request': request})
            return Response({
                "success": True,
                "message": f"Service pricing {'created' if created else 'updated'} successfully.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
            
# pyre-ignore[missing-module]
from ..serializers.category import CategorySerializer

class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing laundry categories.
    """
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]
