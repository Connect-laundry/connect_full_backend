from rest_framework import viewsets, permissions
from rest_framework.response import Response
from django.core.cache import cache
from ..models.special_offer import SpecialOffer
from rest_framework import serializers
from utils.media import SafeMediaModelSerializer

class SpecialOfferSerializer(SafeMediaModelSerializer):
    class Meta:
        model = SpecialOffer
        fields = ['id', 'title', 'description', 'image', 'order', 'valid_until']

class SpecialOfferViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SpecialOffer.objects.filter(is_active=True).order_by('order', '-created_at')
    serializer_class = SpecialOfferSerializer
    permission_classes = [permissions.AllowAny] # Public endpoint

    def list(self, request, *args, **kwargs):
        cache_key = "special_offers_list_v1"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            resp = Response(cached_data)
            resp['Cache-Control'] = 'public, max-age=300, stale-while-revalidate=600'
            return resp
        resp = super().list(request, *args, **kwargs)
        if resp.status_code == 200:
            cache.set(cache_key, resp.data, 300)
            resp['Cache-Control'] = 'public, max-age=300, stale-while-revalidate=600'
        return resp
