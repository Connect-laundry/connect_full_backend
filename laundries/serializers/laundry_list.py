# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from django.db.models import Avg, Count
from ..models.laundry import Laundry
from ..models.favorite import Favorite

class LaundryListSerializer(serializers.ModelSerializer):
    location = serializers.CharField(source='address')
    distance = serializers.SerializerMethodField()
    rating = serializers.FloatField(read_only=True)
    reviewsCount = serializers.IntegerField(read_only=True)
    isOpen = serializers.SerializerMethodField()
    priceRange = serializers.CharField(source='price_range')
    isFavorite = serializers.SerializerMethodField()
    estimatedDelivery = serializers.SerializerMethodField()

    class Meta:
        model = Laundry
        fields = (
            'id', 'name', 'image', 'location', 'distance', 'rating', 
            'reviewsCount', 'isOpen', 'priceRange', 'isFavorite', 'estimatedDelivery'
        )

    def get_distance(self, obj):
        # distance is annotated in the queryset
        return getattr(obj, 'distance', None)

    def get_isOpen(self, obj):
        return True # Placeholder for now

    def get_isFavorite(self, obj):
        user = self.context.get('request').user
        if user.is_authenticated:
            return Favorite.objects.filter(user=user, laundry=obj).exists()
        return False

    def get_estimatedDelivery(self, obj):
        return f"{obj.estimated_delivery_hours}h"
