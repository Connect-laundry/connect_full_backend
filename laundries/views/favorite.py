# pyre-ignore[missing-module]
from rest_framework import generics, permissions
# pyre-ignore[missing-module]
from django.db.models import Avg, Count
# pyre-ignore[missing-module]
from rest_framework.response import Response
# pyre-ignore[missing-module]
from ..models.favorite import Favorite
# pyre-ignore[missing-module]
from ..serializers.laundry_list import LaundryListSerializer
# pyre-ignore[missing-module]
from ..pagination import StandardResultsSetPagination

class FavoriteListView(generics.ListAPIView):
    serializer_class = LaundryListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return Favorite.objects.filter(user=self.request.user).select_related('laundry').annotate(
            rating=Avg('laundry__reviews__rating'),
            reviewsCount=Count('laundry__reviews')
        ).order_by('-created_at').values_list('laundry', flat=True)

    def list(self, request, *args, **kwargs):
        # We need to return Laundry objects, not Favorite IDs
        laundry_ids = self.get_queryset()
        # pyre-ignore[missing-module]
        from ..models.laundry import Laundry
        laundries = Laundry.objects.filter(id__in=laundry_ids).annotate(
            rating=Avg('reviews__rating'),
            reviewsCount=Count('reviews')
        )
        
        page = self.paginate_queryset(laundries)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(laundries, many=True)
        return Response(serializer.data)
