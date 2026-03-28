# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from ..models.laundry import Laundry
# pyre-ignore[missing-module]
from ..models.opening_hours import OpeningHours
# pyre-ignore[missing-module]
from ..models.service import LaundryService
from .review import ReviewSerializer


class OpeningHoursSerializer(serializers.ModelSerializer):
    dayDisplay = serializers.CharField(source='get_day_display', read_only=True)

    class Meta:
        model = OpeningHours
        fields = ('id', 'day', 'dayDisplay', 'opening_time', 'closing_time', 'is_closed')
        read_only_fields = ('id',)


class OwnerLaundrySerializer(serializers.ModelSerializer):
    """
    Full CRUD serializer for laundry owners.
    Supports creating, updating, and reading their own laundry storefront.
    """
    opening_hours = OpeningHoursSerializer(many=True, required=False)
    imageUrl = serializers.SerializerMethodField()
    statusDisplay = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Laundry
        fields = (
            'id', 'name', 'description', 'image', 'imageUrl',
            'address', 'city', 'latitude', 'longitude',
            'phone_number', 'price_range', 'estimated_delivery_hours',
            'delivery_fee', 'pickup_fee', 'min_order',
            'min_weight', 'price_per_kg', 'pricing_methods',
            'is_featured', 'is_active', 'status', 'statusDisplay',
            'opening_hours',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'is_featured', 'is_active', 'status',
            'created_at', 'updated_at',
        )

    def get_imageUrl(self, obj):
        if not obj.image:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url

    def create(self, validated_data):
        hours_data = validated_data.pop('opening_hours', [])
        request = self.context['request']

        with transaction.atomic():
            laundry = Laundry.objects.create(
                owner=request.user,
                **validated_data
            )

            for hour in hours_data:
                OpeningHours.objects.create(laundry=laundry, **hour)

        return laundry

    def update(self, instance, validated_data):
        hours_data = validated_data.pop('opening_hours', None)

        with transaction.atomic():
            # Update laundry fields
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            # Replace opening hours if provided
            if hours_data is not None:
                instance.opening_hours.all().delete()
                for hour in hours_data:
                    OpeningHours.objects.create(laundry=instance, **hour)

        return instance
