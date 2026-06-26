# pyre-ignore[missing-module]
from rest_framework import serializers

from .models import AnalyticsEvent


class AnalyticsEventInSerializer(serializers.Serializer):
    """Inbound event from the mobile client. Kept permissive but bounded."""
    event_name = serializers.CharField(max_length=64)
    session_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default='')
    platform = serializers.ChoiceField(
        choices=AnalyticsEvent.Platform.choices, required=False,
        default=AnalyticsEvent.Platform.UNKNOWN,
    )
    os_version = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    screen_name = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    event_data = serializers.DictField(required=False, default=dict)
    occurred_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


class AnalyticsBatchSerializer(serializers.Serializer):
    """A batch upload — the app flushes its offline queue in one request."""
    events = AnalyticsEventInSerializer(many=True, allow_empty=False)

    def validate_events(self, value):
        if len(value) > 100:
            raise serializers.ValidationError('A batch may contain at most 100 events.')
        return value


class AnalyticsEventOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalyticsEvent
        fields = [
            'id', 'event_name', 'user', 'session_id', 'platform',
            'os_version', 'app_version', 'screen_name', 'event_data',
            'occurred_at', 'created_at',
        ]
