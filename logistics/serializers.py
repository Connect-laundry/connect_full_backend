# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from .models import DeliveryAssignment, TrackingLog

class DeliveryAssignmentSerializer(serializers.ModelSerializer):
    driverEmail = serializers.EmailField(source='driver.email', read_only=True)
    
    class Meta:
        model = DeliveryAssignment
        fields = [
            'id', 'order', 'driver', 'driverEmail', 
            'assignment_type', 'assigned_at', 'completed_at', 'status'
        ]
        read_only_fields = ['id', 'assigned_at']

class TrackingLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingLog
        fields = [
            'id', 'order', 'status', 'description', 
            'location_name', 'latitude', 'longitude', 'timestamp'
        ]
        read_only_fields = ['id', 'timestamp']
