from rest_framework import serializers
from ..models.machine import Machine


class MachineSerializer(serializers.ModelSerializer):
    """Full CRUD serializer for laundry machines."""
    typeDisplay = serializers.CharField(
        source='get_machine_type_display', read_only=True)
    statusDisplay = serializers.CharField(
        source='get_status_display', read_only=True)

    class Meta:
        model = Machine
        fields = (
            'id', 'name', 'machine_type', 'typeDisplay',
            'status', 'statusDisplay', 'notes',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')


class MachineStatusSerializer(serializers.Serializer):
    """For the PATCH /status/ endpoint."""
    status = serializers.ChoiceField(choices=Machine.MachineStatus.choices)
