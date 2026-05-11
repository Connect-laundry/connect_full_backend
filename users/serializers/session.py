# pyre-ignore[missing-module]
from rest_framework import serializers

from users.models import DeviceSession
from users.services.session_service import mask_ip_address


class RefreshTokenRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=True, trim_whitespace=True)


class DeviceSessionSerializer(serializers.ModelSerializer):
    current = serializers.SerializerMethodField()
    ip_address = serializers.SerializerMethodField()

    class Meta:
        model = DeviceSession
        fields = [
            'id',
            'device_id',
            'platform',
            'app_version',
            'user_agent',
            'ip_address',
            'created_at',
            'last_used_at',
            'current',
        ]

    def get_current(self, obj):
        current_session_id = self.context.get('current_session_id')
        return str(obj.id) == str(current_session_id)

    def get_ip_address(self, obj):
        return mask_ip_address(obj.ip_address)
