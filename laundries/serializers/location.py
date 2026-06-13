"""Serializers for the location / geocoding contract.

``LocationInputSerializer`` is the write-only structured input owners use to set
their business location. It supports all four capture methods the frontend
offers (GPS, map pin, address search, manual address). The owner serializer
resolves it into canonical ``latitude``/``longitude`` — which are then exposed
read-only.
"""
# pyre-ignore[missing-module]
from rest_framework import serializers

from ..utils.validators import validate_latitude, validate_longitude


class LocationInputSerializer(serializers.Serializer):
    """Write-only structured location input.

    Supply either coordinates (from GPS / map pin / Places SDK) or an address to
    be geocoded by the backend. ``method`` is an optional hint for analytics.
    """

    METHOD_CHOICES = ('gps', 'pin', 'search', 'address')

    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True,
        validators=[validate_latitude],
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True,
        validators=[validate_longitude],
    )
    address = serializers.CharField(required=False, allow_blank=True)
    place_id = serializers.CharField(required=False, allow_blank=True)
    method = serializers.ChoiceField(choices=METHOD_CHOICES, required=False)

    def validate(self, attrs):
        has_lat = attrs.get('latitude') is not None
        has_lng = attrs.get('longitude') is not None
        if has_lat != has_lng:
            raise serializers.ValidationError(
                'latitude and longitude must be provided together.'
            )
        if not (has_lat or attrs.get('address')):
            raise serializers.ValidationError(
                'Provide coordinates or an address to resolve a location.'
            )
        return attrs


class GeocodeRequestSerializer(serializers.Serializer):
    """Input for the standalone geocode endpoint (forward or reverse)."""

    address = serializers.CharField(required=False, allow_blank=True)
    latitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True,
        validators=[validate_latitude],
    )
    longitude = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True,
        validators=[validate_longitude],
    )

    def validate(self, attrs):
        has_coords = attrs.get('latitude') is not None and attrs.get('longitude') is not None
        if not (attrs.get('address') or has_coords):
            raise serializers.ValidationError(
                'Provide either an address (forward geocode) or '
                'latitude+longitude (reverse geocode).'
            )
        return attrs


class GeocodeResultSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    formatted_address = serializers.CharField(allow_blank=True)
    place_id = serializers.CharField(allow_blank=True)
