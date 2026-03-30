# pyre-ignore[missing-module]
from rest_framework import serializers

# pyre-ignore[missing-module]
from django.db.models import Avg, Count

# pyre-ignore[missing-module]
from ..models.laundry import Laundry

# pyre-ignore[missing-module]
from ..models.service import LaundryService

# pyre-ignore[missing-module]
from ..models.favorite import Favorite

# pyre-ignore[missing-module]
from .review import ReviewSerializer
from .owner import OpeningHoursSerializer


class LaundryServiceSerializer(serializers.ModelSerializer):
    itemName = serializers.CharField(source="item.name", read_only=True)
    itemId = serializers.UUIDField(source="item.id", read_only=True)
    serviceType = serializers.CharField(source="service_type.name", read_only=True)
    serviceTypeId = serializers.UUIDField(source="service_type.id", read_only=True)
    itemCategory = serializers.CharField(
        source="item.item_category.name", read_only=True
    )
    itemCategoryId = serializers.UUIDField(
        source="item.item_category.id", read_only=True
    )
    itemImage = serializers.SerializerMethodField()

    class Meta:
        model = LaundryService
        fields = (
            "id",
            "itemName",
            "itemId",
            "serviceType",
            "serviceTypeId",
            "itemCategory",
            "itemCategoryId",
            "itemImage",
            "price",
            "estimated_duration",
            "is_available",
        )

    def get_itemImage(self, obj):
        if not obj.item.image:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.item.image.url)
        return obj.item.image.url


class LaundryDetailSerializer(serializers.ModelSerializer):
    services = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()
    rating = serializers.FloatField(read_only=True)
    reviewsCount = serializers.IntegerField(read_only=True)
    isFavorite = serializers.SerializerMethodField()
    priceRange = serializers.CharField(source="price_range")
    imageUrl = serializers.SerializerMethodField()
    minOrder = serializers.DecimalField(
        source="min_order", max_digits=10, decimal_places=2, read_only=True
    )
    deliveryFee = serializers.DecimalField(
        source="delivery_fee", max_digits=10, decimal_places=2, read_only=True
    )
    pickupFee = serializers.DecimalField(
        source="pickup_fee", max_digits=10, decimal_places=2, read_only=True
    )
    pricingMethods = serializers.ListField(
        child=serializers.CharField(), source="pricing_methods", read_only=True
    )
    pricePerKg = serializers.DecimalField(
        source="price_per_kg", max_digits=10, decimal_places=2, read_only=True
    )
    minWeight = serializers.DecimalField(
        source="min_weight", max_digits=5, decimal_places=2, read_only=True
    )
    opening_hours = OpeningHoursSerializer(many=True, read_only=True)
    statusDisplay = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Laundry
        fields = (
            "id",
            "name",
            "description",
            "image",
            "imageUrl",
            "address",
            "city",
            "latitude",
            "longitude",
            "phone_number",
            "priceRange",
            "estimated_delivery_hours",
            "is_featured",
            "is_active",
            "status",
            "statusDisplay",
            "services",
            "reviews",
            "rating",
            "reviewsCount",
            "isFavorite",
            "minOrder",
            "deliveryFee",
            "pickupFee",
            "pricingMethods",
            "pricePerKg",
            "minWeight",
            "opening_hours",
        )

    def get_imageUrl(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url

    def get_services(self, obj):
        services = obj.laundry_services.filter(is_available=True).select_related(
            "item", "service_type"
        )
        # pyre-ignore[missing-module]
        return LaundryServiceSerializer(services, many=True, context=self.context).data

    def get_reviews(self, obj):
        reviews = obj.reviews.all()[:5]
        # pyre-ignore[missing-module]
        return ReviewSerializer(reviews, many=True).data

    def get_isFavorite(self, obj):
        user = self.context.get("request").user
        if user.is_authenticated:
            return Favorite.objects.filter(user=user, laundry=obj).exists()
        return False
