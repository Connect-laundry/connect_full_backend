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
    dayDisplay = serializers.CharField(source="get_day_display", read_only=True)

    class Meta:
        model = OpeningHours
        fields = (
            "id",
            "day",
            "dayDisplay",
            "opening_time",
            "closing_time",
            "is_closed",
        )
        read_only_fields = ("id",)


class OwnerLaundrySerializer(serializers.ModelSerializer):
    """
    Full CRUD serializer for laundry owners.
    Supports creating, updating, and reading their own laundry storefront.
    """

    opening_hours = OpeningHoursSerializer(many=True, required=False)
    imageUrl = serializers.SerializerMethodField()
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
            "price_range",
            "estimated_delivery_hours",
            "delivery_fee",
            "pickup_fee",
            "min_order",
            "pricing_methods",
            "price_per_kg",
            "min_weight",
            "is_featured",
            "is_active",
            "status",
            "statusDisplay",
            "opening_hours",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "is_featured",
            "is_active",
            "status",
            "created_at",
            "updated_at",
        )

    pricing_methods = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="['PER_ITEM', 'PER_KG']",
    )

    def validate(self, data):
        """
        Layered validation: Ensure that updates to an active laundry
        don't violate business rules.
        """
        # If the laundry is already active, we must ensure the new data is
        # valid
        instance = self.instance
        if instance and instance.is_active:
            # We create a temporary instance to validate without saving
            # This is a bit tricky with ArrayFields, so we'll check fields
            # directly
            new_methods = data.get("pricing_methods", instance.pricing_methods)
            new_price = data.get("price_per_kg", instance.price_per_kg)

            from ..services.laundry_validation import (
                validate_laundry_ready_for_business,
            )

            # We can't easily pass the instance because it hasn't been updated yet.
            # However, Laundry.clean() is called in update() via instance.save().
            # For extra safety at the serializer level:
            if "PER_KG" in new_methods and (new_price is None or new_price <= 0):
                raise serializers.ValidationError(
                    {
                        "price_per_kg": "Price per kg must be greater than zero for active stores."
                    }
                )

            if "PER_ITEM" in new_methods and not instance.laundry_services.exists():
                raise serializers.ValidationError(
                    {
                        "pricing_methods": "Cannot enable Per Item pricing without items in catalog."
                    }
                )

        return data

    def get_imageUrl(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url

    def create(self, validated_data):
        hours_data = validated_data.pop("opening_hours", [])
        request = self.context["request"]

        try:
            with transaction.atomic():
                laundry = Laundry.objects.create(owner=request.user, **validated_data)

                for hour in hours_data:
                    OpeningHours.objects.create(laundry=laundry, **hour)

            return laundry
        except Exception as e:
            # Handle model level validation errors (raised in Laundry.save() ->
            # full_clean())
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(e, DjangoValidationError):
                raise serializers.ValidationError(e.message_dict)
            raise e

    def update(self, instance, validated_data):
        hours_data = validated_data.pop("opening_hours", None)

        try:
            with transaction.atomic():
                # Update laundry fields
                for attr, value in validated_data.items():
                    setattr(instance, attr, value)

                # This will trigger Laundry.clean() via Laundry.save() ->
                # full_clean()
                instance.save()

                # Replace opening hours if provided
                if hours_data is not None:
                    instance.opening_hours.all().delete()
                    for hour in hours_data:
                        OpeningHours.objects.create(laundry=instance, **hour)

            return instance
        except Exception as e:
            # Handle model level validation errors
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(e, DjangoValidationError):
                raise serializers.ValidationError(e.message_dict)
            raise e
