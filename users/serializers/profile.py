# pyre-ignore[missing-module]
from rest_framework import serializers
from ..models import User, Address

class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            'id', 'label', 'address_line1', 'city', 
            'latitude', 'longitude', 'is_default', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        user = self.context['request'].user
        return Address.objects.create(user=user, **validated_data)

class ProfileSerializer(serializers.ModelSerializer):
    addresses = AddressSerializer(many=True, read_only=True)
    fullName = serializers.CharField(source='get_full_name', read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'phone', 'first_name', 'last_name', 
            'fullName', 'avatar', 'role', 'addresses', 'created_at'
        ]
        read_only_fields = ['id', 'email', 'role', 'created_at']
