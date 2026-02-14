# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from django.db.models import Avg, Count
# pyre-ignore[missing-module]
from django.core.cache import cache
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.favorite import Favorite
# pyre-ignore[missing-module]
from ..models.opening_hours import OpeningHours

class LaundryListSerializer(serializers.ModelSerializer):
    location = serializers.CharField(source='address')
    distance = serializers.SerializerMethodField()
    rating = serializers.FloatField(read_only=True)
    reviewsCount = serializers.IntegerField(read_only=True)
    isOpen = serializers.SerializerMethodField()
    priceRange = serializers.CharField(source='price_range')
    isFavorite = serializers.SerializerMethodField()
    minOrder = serializers.DecimalField(source='min_order', max_digits=10, decimal_places=2, read_only=True)
    deliveryFee = serializers.DecimalField(source='delivery_fee', max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Laundry
        fields = (
            'id', 'name', 'image', 'location', 'distance', 'rating', 
            'reviewsCount', 'isOpen', 'priceRange', 'isFavorite', 'estimatedDelivery',
            'minOrder', 'deliveryFee'
        )

    def get_distance(self, obj):
        # distance is annotated in the queryset
        return getattr(obj, 'distance', None)

    def get_isOpen(self, obj):
        cache_key = f"laundry_is_open_{obj.id}"
        is_open = cache.get(cache_key)
        
        if is_open is not None:
            return is_open
            
        now = timezone.localtime()
        current_day = now.isoweekday() # 1-7
        current_time = now.time()
        
        # Check OpeningHours
        oh = OpeningHours.objects.filter(laundry=obj, day=current_day, is_closed=False).first()
        is_open_now = False
        if oh:
            if oh.opening_time <= current_time <= oh.closing_time:
                is_open_now = True
        
        cache.set(cache_key, is_open_now, 300) # 5 minutes
        return is_open_now

    def get_isFavorite(self, obj):
        user = self.context.get('request').user
        if user.is_authenticated:
            return Favorite.objects.filter(user=user, laundry=obj).exists()
        return False

    def get_estimatedDelivery(self, obj):
        # pyre-ignore[missing-module]
        from ..services.delivery_estimator import DeliveryEstimator
        
        # 1. Get user location from context if available
        request = self.context.get('request')
        user_lat = request.query_params.get('lat') if request else None
        user_lng = request.query_params.get('lng') if request else None

        # 2. Get active order count (either annotated or via cached lookup)
        # Check if already annotated (efficient)
        active_order_count = getattr(obj, 'active_order_count', None)
        
        if active_order_count is None:
            # Fallback for retrieve or cases where not annotated
            cache_key = f"laundry_active_orders_{obj.id}"
            active_order_count = cache.get(cache_key)
            
            if active_order_count is None:
                # pyre-ignore[missing-module]
                from ordering.models import Order
                active_order_count = Order.objects.filter(
                    laundry=obj, 
                    status__in=['PENDING', 'PICKED_UP', 'WASHING']
                ).count()
                cache.set(cache_key, active_order_count, 120) # 2 minutes

        # 3. Calculate dynamic estimation
        estimator = DeliveryEstimator()
        return estimator.get_estimated_delivery_time(
            obj, 
            user_lat=user_lat, 
            user_lng=user_lng, 
            active_order_count=active_order_count
        )
