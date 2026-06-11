"""Serializers for the owner-facing "My Laundry" management feature."""
import json
from datetime import time

# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from rest_framework import serializers

from ..models.laundry import Laundry
from ..models.opening_hours import OpeningHours


class OpeningHoursSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    # Times are optional so a closed day can be sent without hours.
    opening_time = serializers.TimeField(required=False, allow_null=True)
    closing_time = serializers.TimeField(required=False, allow_null=True)

    class Meta:
        model = OpeningHours
        fields = ['id', 'day', 'opening_time', 'closing_time', 'is_closed']

    def validate(self, attrs):
        is_closed = attrs.get('is_closed', False)
        opening = attrs.get('opening_time')
        closing = attrs.get('closing_time')

        if is_closed:
            # Model fields are NOT NULL; default closed days to midnight.
            attrs['opening_time'] = opening or time(0, 0)
            attrs['closing_time'] = closing or time(0, 0)
            return attrs

        if not opening or not closing:
            raise serializers.ValidationError(
                'opening_time and closing_time are required for open days.'
            )
        if opening >= closing:
            raise serializers.ValidationError(
                'opening_time must be earlier than closing_time.'
            )
        return attrs


class MyLaundrySerializer(serializers.ModelSerializer):
    """Read/write serializer for an owner's own laundry profile."""

    imageUrl = serializers.SerializerMethodField()
    operating_hours = OpeningHoursSerializer(
        many=True, source='opening_hours', required=False
    )

    class Meta:
        model = Laundry
        fields = [
            'id', 'name', 'description', 'image', 'imageUrl', 'address', 'city',
            'latitude', 'longitude', 'phone_number', 'price_range',
            'estimated_delivery_hours', 'delivery_fee', 'pickup_fee', 'min_order',
            'is_featured', 'is_active', 'status', 'approved_at', 'rejected_at',
            'operating_hours', 'created_at', 'updated_at',
        ]
        # These are controlled by the platform/approval flow, never the owner.
        read_only_fields = [
            'id', 'imageUrl', 'is_featured', 'is_active', 'status',
            'approved_at', 'rejected_at', 'created_at', 'updated_at',
        ]

    def get_imageUrl(self, obj) -> str | None:
        if not obj.image:
            return None
        request = self.context.get('request')
        try:
            url = obj.image.url
        except (ValueError, AttributeError):
            return None
        if request:
            return request.build_absolute_uri(url)
        return url

    def to_internal_value(self, data):
        """Accept ``operating_hours`` as a JSON string (multipart/form-data)."""
        data = self._coerce_operating_hours(data)
        return super().to_internal_value(data)

    @staticmethod
    def _coerce_operating_hours(data):
        if not hasattr(data, 'get'):
            return data
        raw = data.get('operating_hours')
        if not isinstance(raw, str):
            return data
        stripped = raw.strip()
        if stripped == '':
            return data
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            raise serializers.ValidationError(
                {'operating_hours': ['Must be a valid JSON array.']}
            )
        # Build a plain mutable dict so the nested list survives validation
        # regardless of whether the source was a QueryDict or a plain dict.
        coerced = {key: data.get(key) for key in data.keys()}
        coerced['operating_hours'] = parsed
        return coerced

    def create(self, validated_data):
        opening_hours = validated_data.pop('opening_hours', [])
        request = self.context['request']
        with transaction.atomic():
            laundry = Laundry.objects.create(
                owner=request.user,
                status=Laundry.ApprovalStatus.PENDING,
                is_active=False,
                is_featured=False,
                **validated_data,
            )
            self._sync_opening_hours(laundry, opening_hours)
        return laundry

    def update(self, instance, validated_data):
        opening_hours = validated_data.pop('opening_hours', None)
        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            if opening_hours is not None:
                self._sync_opening_hours(instance, opening_hours)
        return instance

    @staticmethod
    def _sync_opening_hours(laundry, hours_data):
        """Upsert provided days; the supplied set is authoritative."""
        provided_days = set()
        for entry in hours_data:
            day = entry['day']
            provided_days.add(day)
            OpeningHours.objects.update_or_create(
                laundry=laundry,
                day=day,
                defaults={
                    'opening_time': entry['opening_time'],
                    'closing_time': entry['closing_time'],
                    'is_closed': entry.get('is_closed', False),
                },
            )
        laundry.opening_hours.exclude(day__in=provided_days).delete()
