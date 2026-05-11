# pyre-ignore[missing-module]
from django.contrib.auth import authenticate
# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from rest_framework.exceptions import AuthenticationFailed

from .session_service import issue_tokens_for_user

class AuthService:
    @staticmethod
    def get_tokens_for_user(user, request):
        tokens = issue_tokens_for_user(user, request)
        return {
            'refresh': tokens['refresh'],
            'access': tokens['access'],
        }

    def register_user(self, validated_data, request):
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        
        # pyre-ignore[missing-module]
        from ..models import User
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        
        return user, self.get_tokens_for_user(user, request)

    def login_user(self, email, password, request):
        user = authenticate(email=email, password=password)
        
        if not user:
            raise AuthenticationFailed("Invalid email or password.")
            
        if not user.is_active:
            raise AuthenticationFailed("User account is disabled.")
            
        return user, self.get_tokens_for_user(user, request)
