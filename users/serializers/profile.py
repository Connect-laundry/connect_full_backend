# pyre-ignore[missing-module]
from rest_framework import serializers
from utils.media import SafeMediaModelSerializer
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

class ProfileSerializer(SafeMediaModelSerializer):
    addresses = AddressSerializer(many=True, read_only=True)
    fullName = serializers.CharField(source='get_full_name', read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'phone', 'first_name', 'last_name', 
            'fullName', 'avatar', 'role', 'addresses', 'social_provider',
            'social_profile_image_url', 'last_social_login_at', 'created_at'
        ]
        read_only_fields = [
            'id', 'email', 'role', 'social_provider', 'social_profile_image_url',
            'last_social_login_at', 'created_at',
        ]
