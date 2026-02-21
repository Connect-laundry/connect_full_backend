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

class AuthService:
    @staticmethod
    def get_tokens_for_user(user):
        refresh = RefreshToken.for_user(user)
        
        # Custom claims
        refresh['email'] = user.email
        refresh['role'] = user.role
        
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }

    def register_user(self, validated_data):
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        
        return user, self.get_tokens_for_user(user)

    def login_user(self, email, password):
        user = authenticate(email=email, password=password)
        
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
            
        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")
            
        return user, self.get_tokens_for_user(user)
