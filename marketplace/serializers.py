# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from .models import Notification

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id', 'title', 'body', 'type', 
            'is_read', 'created_at', 'read_at', 'related_order'
        ]
        read_only_fields = ['id', 'created_at', 'read_at']
