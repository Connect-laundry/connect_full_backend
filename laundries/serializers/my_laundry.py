"""Serializers for the owner-facing "My Laundry" management feature."""
import json
from datetime import time

# pyre-ignore[missing-module]
from django.db import transaction
# pyre-ignore[missing-module]
from django.utils import timezone
# pyre-ignore[missing-module]
from rest_framework import serializers

from utils.media import (
    UNSET,
    SafeMediaModelSerializer,
    safe_media_url,
    save_optional_media,
)

from ..models.laundry import Laundry
from ..models.opening_hours import OpeningHours, HolidayOverride
from ..services.geocoding import GeocodingError, GeocodingUnavailable, get_geocoder
from .location import LocationInputSerializer


# Industry-standard default operating hours used to seed the onboarding form.
# Day numbers follow OpeningHours.Weekday (1=Mon ... 7=Sun).
OPERATING_HOURS_DEFAULT_TEMPLATE = [
    {'day': 1, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
    {'day': 2, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
    {'day': 3, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
    {'day': 4, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
    {'day': 5, 'opening_time': '08:00', 'closing_time': '18:00', 'is_closed': False},
    {'day': 6, 'opening_time': '09:00', 'closing_time': '15:00', 'is_closed': False},
    {'day': 7, 'opening_time': '00:00', 'closing_time': '00:00', 'is_closed': True},
]


class HolidayOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = HolidayOverride
        fields = ['id', 'date', 'opening_time', 'closing_time', 'is_closed', 'note']
        read_only_fields = ['id']

    def validate(self, attrs):
        is_closed = attrs.get('is_closed', False)
        is_overnight = attrs.get('is_overnight', False)
        opening = attrs.get('opening_time')
        closing = attrs.get('closing_time')

        if is_closed:
            attrs['opening_time'] = None
            attrs['closing_time'] = None
            return attrs

        if not opening or not closing:
            raise serializers.ValidationError(
                'opening_time and closing_time are required for open days.'
            )

        if is_overnight:
            # Spans midnight (e.g. 20:00 -> 02:00): closing is on the next day, so
            # closing < opening is expected. Only equal times are nonsensical.
            if opening == closing:
                raise serializers.ValidationError(
                    'Overnight hours cannot have equal opening and closing times.'
                )
        elif opening >= closing:
            raise serializers.ValidationError(
                'opening_time must be earlier than closing_time '
                '(set is_overnight=true for hours that cross midnight).'
            )
        return attrs


class OpeningHoursSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(read_only=True)
    # Times are optional so a closed day can be sent without hours.
    opening_time = serializers.TimeField(required=False, allow_null=True)
    closing_time = serializers.TimeField(required=False, allow_null=True)
    is_overnight = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = OpeningHours
        fields = ['id', 'day', 'opening_time', 'closing_time', 'is_closed', 'is_overnight']

    def validate(self, attrs):
        is_closed = attrs.get('is_closed', False)
        is_overnight = attrs.get('is_overnight', False)
        opening = attrs.get('opening_time')
        closing = attrs.get('closing_time')

        if is_closed:
            # Model fields are NOT NULL; default closed days to midnight.
            attrs['opening_time'] = opening or time(0, 0)
            attrs['closing_time'] = closing or time(0, 0)
            attrs['is_overnight'] = False
            return attrs

        if not opening or not closing:
            raise serializers.ValidationError(
                'opening_time and closing_time are required for open days.'
            )

        if is_overnight:
            # Spans midnight (e.g. 20:00 -> 02:00): closing is on the next day, so
            # closing < opening is expected. Only equal times are nonsensical.
            if opening == closing:
                raise serializers.ValidationError(
                    'Overnight hours cannot have equal opening and closing times.'
                )
        elif opening >= closing:
            raise serializers.ValidationError(
                'opening_time must be earlier than closing_time '
                '(set is_overnight=true for hours that cross midnight).'
            )
        return attrs


class MyLaundrySerializer(SafeMediaModelSerializer):
    """Read/write serializer for an owner's own laundry profile."""

    imageUrl = serializers.SerializerMethodField()
    operating_hours = OpeningHoursSerializer(
        many=True, source='opening_hours', required=False
    )
    # Write-only structured location input. ``latitude``/``longitude`` are
    # exposed read-only (derived) — owners set location through this field.
    location = LocationInputSerializer(write_only=True, required=False)

    class Meta:
        model = Laundry
        fields = [
            'id', 'name', 'description', 'image', 'imageUrl', 'address', 'city',
            'latitude', 'longitude', 'location', 'phone_number', 'price_range',
            'pricing_model', 'estimated_delivery_hours', 'delivery_fee', 'pickup_fee',
            'min_order', 'is_featured', 'is_active', 'status', 'approved_at',
            'rejected_at', 'operating_hours', 'created_at', 'updated_at',
            'vacation_mode', 'service_radius_km', 'service_area_polygon',
            'is_eco_friendly', 'ironing_available',
        ]
        # latitude/longitude are backend-derived (set via ``location``), so they
        # are read-only output. The rest are platform/approval-controlled.
        read_only_fields = [
            'id', 'imageUrl', 'latitude', 'longitude', 'is_featured', 'is_active',
            'status', 'approved_at', 'rejected_at', 'created_at', 'updated_at',
        ]

    def get_imageUrl(self, obj) -> str | None:
        return safe_media_url(obj.image, self.context.get('request'))

    def to_internal_value(self, data):
        """Normalise multipart inputs and fold legacy coordinate fields.

        * ``operating_hours`` may arrive as a JSON string (multipart/form-data).
        * ``location`` may arrive as a JSON string (multipart/form-data).
        * Legacy clients send top-level ``latitude``/``longitude``; fold them into
          the structured ``location`` input for backward compatibility.
        """
        data = self._coerce_json_field(data, 'operating_hours', expect_list=True)
        data = self._coerce_json_field(data, 'location', expect_list=False)
        data = self._fold_legacy_coordinates(data)
        return super().to_internal_value(data)

    @staticmethod
    def _coerce_json_field(data, key, *, expect_list):
        if not hasattr(data, 'get'):
            return data
        raw = data.get(key)
        if not isinstance(raw, str):
            return data
        stripped = raw.strip()
        if stripped == '':
            return data
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            label = 'a valid JSON array.' if expect_list else 'valid JSON.'
            raise serializers.ValidationError({key: [f'Must be {label}']})
        coerced = {k: data.get(k) for k in data.keys()}
        coerced[key] = parsed
        return coerced

    @staticmethod
    def _fold_legacy_coordinates(data):
        if not hasattr(data, 'get'):
            return data
        if data.get('location') not in (None, ''):
            return data  # explicit structured location wins
        lat = data.get('latitude')
        lng = data.get('longitude')
        if lat in (None, '') or lng in (None, ''):
            return data
        coerced = {k: data.get(k) for k in data.keys()}
        coerced['location'] = {'latitude': lat, 'longitude': lng, 'method': 'gps'}
        return coerced

    def validate(self, attrs):
        location = attrs.pop('location', None)
        coords = self._resolve_coordinates(location, attrs)
        if coords is not None:
            attrs['latitude'], attrs['longitude'] = coords
        elif self.instance is None:
            # Creation requires a resolvable business location.
            raise serializers.ValidationError(
                {'location': ['A business location (coordinates or address) is required.']}
            )
        return attrs

    def _resolve_coordinates(self, location, attrs):
        """Return (latitude, longitude) Decimals or None if unset on update."""
        if location:
            lat = location.get('latitude')
            lng = location.get('longitude')
            if lat is not None and lng is not None:
                return lat, lng
            address = location.get('address') or attrs.get('address')
            if address:
                return self._geocode(address)
        # No structured location; on create try the plain address field.
        if self.instance is None and attrs.get('address'):
            return self._geocode(attrs['address'])
        return None

    @staticmethod
    def _geocode(address):
        try:
            result = get_geocoder().geocode(address)
        except GeocodingUnavailable as exc:
            raise serializers.ValidationError(
                {'location': [
                    'Address geocoding is unavailable on this server; '
                    'please supply latitude and longitude coordinates.'
                ]}
            ) from exc
        except GeocodingError as exc:
            raise serializers.ValidationError(
                {'location': [f'Could not resolve the address to a location: {exc}']}
            ) from exc
        return result.latitude, result.longitude

    def create(self, validated_data):
        opening_hours = validated_data.pop('opening_hours', [])
        # The logo is optional. Keep it out of the row-creating transaction so a
        # storage outage (e.g. set-but-invalid Cloudinary creds) degrades to
        # "registered without a logo" instead of failing the whole registration
        # with an unhandled 500.
        image = validated_data.pop('image', None)
        request = self.context['request']
        with transaction.atomic():
            laundry = Laundry.objects.create(
                owner=request.user,
                status=Laundry.ApprovalStatus.PENDING,
                is_active=False,
                is_featured=False,
                submitted_at=timezone.now(),
                **validated_data,
            )
            self._sync_opening_hours(laundry, opening_hours)
        if image:
            save_optional_media(
                laundry, 'image', image, request=request,
                laundry_id=str(laundry.id),
            )
        return laundry

    def update(self, instance, validated_data):
        opening_hours = validated_data.pop('opening_hours', None)
        # ``UNSET`` => the client did not touch the logo; ``None`` => clear it.
        image = validated_data.pop('image', UNSET)
        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            if opening_hours is not None:
                self._sync_opening_hours(instance, opening_hours)
        # An edit after "changes requested" (or rejection) automatically puts
        # the laundry back into the admin review queue.
        if instance.status in (
            Laundry.ApprovalStatus.CHANGES_REQUESTED,
            Laundry.ApprovalStatus.REJECTED,
        ):
            from ..services.approval import LaundryApprovalService
            request = self.context.get('request')
            instance = LaundryApprovalService.resubmit(
                instance, actor=getattr(request, 'user', None)
            )
        if image is not UNSET:
            if image:
                # Storage failure leaves the existing logo untouched (a new
                # upload only replaces it on a successful write).
                save_optional_media(
                    instance, 'image', image, request=self.context.get('request'),
                    laundry_id=str(instance.id),
                )
            else:
                instance.image = None
                instance.save(update_fields=['image'])
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
                    'is_overnight': entry.get('is_overnight', False),
                },
            )
        laundry.opening_hours.exclude(day__in=provided_days).delete()


class CopyTodayHoursSerializer(serializers.Serializer):
    day = serializers.IntegerField(
        min_value=1,
        max_value=7,
        help_text="Day index (1=Monday ... 7=Sunday) from which to copy operating hours."
    )


class ToggleVacationModeResponseSerializer(serializers.Serializer):
    vacation_mode = serializers.BooleanField(
        help_text="The updated vacation mode status of the laundry."
    )

