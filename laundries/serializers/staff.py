from rest_framework import serializers
from ..models.staff import LaundryStaff


class LaundryStaffSerializer(serializers.ModelSerializer):
    """Full serializer for staff member management."""
    roleDisplay = serializers.CharField(source='get_role_display', read_only=True)
    inviteStatusDisplay = serializers.CharField(source='get_invite_status_display', read_only=True)

    class Meta:
        model = LaundryStaff
        fields = (
            'id', 'name', 'email', 'phone',
            'role', 'roleDisplay',
            'invite_status', 'inviteStatusDisplay',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'invite_status', 'created_at', 'updated_at')


class StaffInviteSerializer(serializers.Serializer):
    """For inviting a new staff member."""
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    role = serializers.ChoiceField(choices=LaundryStaff.StaffRole.choices)


class StaffRoleUpdateSerializer(serializers.Serializer):
    """For reassigning a staff member's role."""
    role = serializers.ChoiceField(choices=LaundryStaff.StaffRole.choices)
