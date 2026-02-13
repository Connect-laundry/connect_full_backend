# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions, status
# pyre-ignore[missing-module]
from rest_framework.decorators import action
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from django.db.models import Avg, Count, F, ExpressionWrapper, FloatField
# pyre-ignore[missing-module]
from django.db.models.functions import Sqrt, Sin, Cos, ASin, Radians
# pyre-ignore[missing-module]
from django_filters.rest_framework import DjangoFilterBackend
# pyre-ignore[missing-module]
from rest_framework.filters import SearchFilter

from ..models.laundry import Laundry
from ..models.favorite import Favorite
from ..serializers.laundry_list import LaundryListSerializer
from ..serializers.laundry_detail import LaundryDetailSerializer
from ..pagination import StandardResultsSetPagination
from ..filters import LaundryFilter

class LaundryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Laundry.objects.filter(is_active=True).annotate(
        rating=Avg('reviews__rating'),
        reviewsCount=Count('reviews')
    )
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = LaundryFilter
    search_fields = ['name', 'description', 'address']
    permission_classes = [permissions.AllowAny]

    def get_serializer_class(self):
        if self.action == 'list':
            return LaundryListSerializer
        return LaundryDetailSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        
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
