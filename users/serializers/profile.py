# pyre-ignore[missing-module]
from rest_framework import serializers
from utils.media import SafeMediaModelSerializer
from ..models import User, Address
from ..utils.phone import normalize_phone, PhoneValidationError

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

    def validate_phone(self, value):
        """Normalize to E.164 and enforce uniqueness gracefully.

        The model's ``phone`` column is ``unique``; without this guard a PATCH
        with a number already linked to another account would surface as a
        raw IntegrityError (HTTP 500). Here it becomes a clean 400.
        """
        if value in (None, ''):
            raise serializers.ValidationError('Phone number is required.')
        try:
            e164 = normalize_phone(value)
        except PhoneValidationError as exc:
            raise serializers.ValidationError(str(exc))

        conflict = User.objects.filter(phone=e164)
        if self.instance is not None:
            conflict = conflict.exclude(pk=self.instance.pk)
        if conflict.exists():
            raise serializers.ValidationError(
                'This phone number is already linked to another account.'
            )
        return e164
