from rest_framework import serializers

from users.models import User
from users.services.clerk_service import ALLOWED_SOCIAL_ROLES


class SocialLoginSerializer(serializers.Serializer):
    clerk_token = serializers.CharField(write_only=True, required=True)
    role = serializers.ChoiceField(
        choices=tuple((role, role) for role in ALLOWED_SOCIAL_ROLES),
        required=False,
        default=User.Role.CUSTOMER,
    )


class SocialSessionSerializer(serializers.Serializer):
    authenticated = serializers.BooleanField()
    user = serializers.DictField()
