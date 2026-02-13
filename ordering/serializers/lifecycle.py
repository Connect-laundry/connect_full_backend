# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from ..models.base import OrderStatusHistory

class OrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source='changed_by.get_full_name', read_only=True)
    
    class Meta:
        model = OrderStatusHistory
        fields = [
            'id', 
            'previous_status', 
            'new_status', 
            'changed_by_name', 
            'timestamp', 
            'metadata'
        ]
        read_only_fields = fields

class OrderTransitionSerializer(serializers.Serializer):
    """Generic serializer for status transitions that may require a reason."""
    reason = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)
