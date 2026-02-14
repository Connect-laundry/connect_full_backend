# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from django.db.models import Avg, Count
# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.service import Service
# pyre-ignore[missing-module]
from ..models.favorite import Favorite
# pyre-ignore[missing-module]
from .review import ReviewSerializer

class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ('id', 'name', 'description', 'base_price', 'category')

class LaundryDetailSerializer(serializers.ModelSerializer):
    services = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()
    rating = serializers.FloatField(read_only=True)
    reviewsCount = serializers.IntegerField(read_only=True)
    isFavorite = serializers.SerializerMethodField()
    priceRange = serializers.CharField(source='price_range')

    class Meta:
        model = Laundry
        fields = (
            'id', 'name', 'description', 'image', 'address', 'latitude', 
            'longitude', 'phone_number', 'priceRange', 'estimated_delivery_hours',
            'is_featured', 'services', 'reviews', 'rating', 'reviewsCount', 'isFavorite'
        )

    def get_services(self, obj):
        # We'll group them by category in the view or return flat list
        services = obj.services.filter(is_active=True).select_related('category')
        # pyre-ignore[missing-module]
        return ServiceSerializer(services, many=True).data

    def get_reviews(self, obj):
        reviews = obj.reviews.all()[:5]
        # pyre-ignore[missing-module]
        return ReviewSerializer(reviews, many=True).data

    def get_isFavorite(self, obj):
        user = self.context.get('request').user
        if user.is_authenticated:
            return Favorite.objects.filter(user=user, laundry=obj).exists()
        return False
