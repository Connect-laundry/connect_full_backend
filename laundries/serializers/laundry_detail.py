# pyre-ignore[missing-module]
from rest_framework import serializers
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.service import LaundryService
# pyre-ignore[missing-module]
from ..models.favorite import Favorite
# pyre-ignore[missing-module]
from .review import ReviewSerializer
# pyre-ignore[missing-module]
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from ..models.opening_hours import OpeningHours

class LaundryServiceSerializer(serializers.ModelSerializer):
    itemName = serializers.CharField(source='item.name', read_only=True)
    itemId = serializers.UUIDField(source='item.id', read_only=True)
    serviceType = serializers.CharField(source='service_type.name', read_only=True)
    serviceTypeId = serializers.UUIDField(source='service_type.id', read_only=True)
    itemCategory = serializers.CharField(source='item.item_category.name', read_only=True)
    itemCategoryId = serializers.UUIDField(source='item.item_category.id', read_only=True)
    itemImage = serializers.SerializerMethodField()

    class Meta:
        model = LaundryService
        fields = (
            'id', 'itemName', 'itemId', 'serviceType', 'serviceTypeId', 
            'itemCategory', 'itemCategoryId', 'itemImage', 
            'price', 'estimated_duration', 'is_available'
        )

    @extend_schema_field(OpenApiTypes.URI)
    def get_itemImage(self, obj):
        if not obj.item.image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.item.image.url)
        return obj.item.image.url

# pyre-ignore[missing-module]
from django.core.cache import cache
from ..services.opening_status import is_laundry_open_now

class OpeningHoursDetailSerializer(serializers.ModelSerializer):
    dayDisplay = serializers.SerializerMethodField()

    class Meta:
        model = OpeningHours
        fields = ('id', 'day', 'dayDisplay', 'opening_time', 'closing_time', 'is_closed', 'is_overnight')

    @extend_schema_field(OpenApiTypes.STR)
    def get_dayDisplay(self, obj):
        return obj.get_day_display()

class LaundryDetailSerializer(serializers.ModelSerializer):
    services = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()
    rating = serializers.FloatField(read_only=True)
    reviewsCount = serializers.IntegerField(read_only=True)
    isFavorite = serializers.SerializerMethodField()
    priceRange = serializers.CharField(source='price_range')
    pricingModel = serializers.CharField(source='pricing_model', read_only=True)
    weightPricing = serializers.SerializerMethodField()
    imageUrl = serializers.SerializerMethodField()
    minOrder = serializers.DecimalField(source='min_order', max_digits=10, decimal_places=2, read_only=True)
    deliveryFee = serializers.DecimalField(source='delivery_fee', max_digits=10, decimal_places=2, read_only=True)
    pickup_fee = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    pickupFee = serializers.DecimalField(source='pickup_fee', max_digits=10, decimal_places=2, read_only=True)

    phone = serializers.CharField(source='phone_number', read_only=True)
    opening_hours = OpeningHoursDetailSerializer(many=True, read_only=True)
    isOpen = serializers.SerializerMethodField()
    features = serializers.SerializerMethodField()
    tagline = serializers.SerializerMethodField()

    class Meta:
        model = Laundry
        fields = (
            'id', 'name', 'description', 'image', 'imageUrl', 'address', 'latitude',
            'longitude', 'phone_number', 'phone', 'priceRange', 'pricingModel', 'weightPricing', 'estimated_delivery_hours',
            'is_featured', 'services', 'reviews', 'rating', 'reviewsCount', 'isFavorite',
            'minOrder', 'deliveryFee', 'pickup_fee', 'pickupFee', 'opening_hours', 'isOpen', 'features', 'tagline'
        )


    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_weightPricing(self, obj):
        if obj.pricing_model in ['BY_WEIGHT', 'HYBRID'] and hasattr(obj, 'weight_pricing') and obj.weight_pricing:
            from .pricing import LaundryWeightPricingSerializer
            return LaundryWeightPricingSerializer(obj.weight_pricing, context=self.context).data
        return None

    @extend_schema_field(OpenApiTypes.URI)
    def get_imageUrl(self, obj):
        if not obj.image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url

    @extend_schema_field(LaundryServiceSerializer(many=True))
    def get_services(self, obj):
        services = obj.laundry_services.filter(is_available=True).select_related('item', 'service_type')
        # pyre-ignore[missing-module]
        return LaundryServiceSerializer(services, many=True, context=self.context).data

    @extend_schema_field(ReviewSerializer(many=True))
    def get_reviews(self, obj):
        reviews = obj.reviews.all()[:5]
        # pyre-ignore[missing-module]
        return ReviewSerializer(reviews, many=True).data

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_isFavorite(self, obj):
        user = self.context.get('request').user
        if user.is_authenticated:
            return Favorite.objects.filter(user=user, laundry=obj).exists()
        return False

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_isOpen(self, obj):
        cache_key = f"laundry_is_open_{obj.id}"
        is_open = cache.get(cache_key)
        if is_open is not None:
            return is_open

        is_open_now = is_laundry_open_now(obj)
        cache.set(cache_key, is_open_now, 300)
        return is_open_now

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_features(self, obj):
        items = []
        if obj.is_eco_friendly:
            items.append("Eco-Friendly")
        if obj.ironing_available:
            items.append("Ironing Service Available")
        if obj.pricing_model == 'BY_WEIGHT':
            items.append("Weight-Based Pricing")
        elif obj.pricing_model == 'BY_ITEM':
            items.append("Item-Based Pricing")
        elif obj.pricing_model == 'HYBRID':
            items.append("Hybrid pricing (item + weight)")
        if obj.estimated_delivery_hours:
            items.append(f"Turnaround time: {obj.estimated_delivery_hours} hours")
        return items

    @extend_schema_field(OpenApiTypes.STR)
    def get_tagline(self, obj):
        price_range = obj.price_range or "$$"
        pm_map = {
            'BY_ITEM': 'Item-Based',
            'BY_WEIGHT': 'Weight-Based',
            'HYBRID': 'Hybrid'
        }
        pm_display = pm_map.get(obj.pricing_model, 'Item-Based')
        return f"{price_range} pricing • {pm_display}"

