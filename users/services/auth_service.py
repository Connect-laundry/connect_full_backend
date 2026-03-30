# pyre-ignore[missing-module]
from django.contrib.auth import authenticate

# pyre-ignore[missing-module]
from rest_framework_simplejwt.tokens import RefreshToken

# pyre-ignore[missing-module]
from rest_framework import serializers

# pyre-ignore[missing-module]
from django.conf import settings

# pyre-ignore[missing-module]
from django.core.cache import cache

# pyre-ignore[missing-module]
from ..models import User

import logging

logger = logging.getLogger(__name__)


class AuthService:
    @staticmethod
    def get_tokens_for_user(user):
        refresh = RefreshToken.for_user(user)

        # Custom claims
        refresh["email"] = user.email
        refresh["role"] = user.role

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

    def register_user(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")

        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()

        return user, self.get_tokens_for_user(user)

    def login_user(self, email, password, request=None):
        try:
            user = authenticate(request=request, email=email, password=password)
        except Exception as e:
            logger.error(f"Authentication backend error during login for {email}: {str(e)}", exc_info=True)
            raise serializers.ValidationError("Authentication service temporarily unavailable. Please try again.")

        if not user:
            # Check if the user exists and is inactive to return a specific "disabled" message
            # logic required by regression tests.
            temp_user = User.objects.filter(email=email).first()
            if temp_user and temp_user.check_password(password) and not temp_user.is_active:
                raise serializers.ValidationError("User account is disabled.")
            raise serializers.ValidationError("Invalid email or password.")

        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")

        try:
            tokens = self.get_tokens_for_user(user)
        except Exception as e:
            logger.error(f"Token generation failed for user {user.id}: {str(e)}", exc_info=True)
            raise serializers.ValidationError("Failed to generate authentication tokens. Please try again.")

        return user, tokens
