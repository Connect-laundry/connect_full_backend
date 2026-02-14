# pyre-ignore[missing-module]
from rest_framework import viewsets, permissions
# pyre-ignore[missing-module]
from ..models.special_offer import SpecialOffer
# pyre-ignore[missing-module]
from rest_framework import serializers

class SpecialOfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpecialOffer
        fields = ['id', 'title', 'description', 'image', 'order', 'valid_until']

class SpecialOfferViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SpecialOffer.objects.filter(is_active=True).order_by('order', '-created_at')
    serializer_class = SpecialOfferSerializer
    permission_classes = [permissions.AllowAny] # Public endpoint
