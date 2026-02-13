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
import random
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

    def generate_otp(self, email):
        """Generate a 6-digit OTP and store it in Redis."""
        otp = str(random.randint(100000, 999999))
        cache_key = f"otp_{email}"
        # Use settings for expiry or default to 5 mins
        expiry = getattr(settings, 'OTP_EXPIRY_SECONDS', 300)
        cache.set(cache_key, otp, expiry)
        return otp

    def verify_otp(self, email, otp):
        """Verify an OTP from Redis."""
        cache_key = f"otp_{email}"
        stored_otp = cache.get(cache_key)
        
        if stored_otp and str(stored_otp) == str(otp):
            cache.delete(cache_key)
            return True
        return False

    def login_user(self, email, password):
        user = authenticate(email=email, password=password)
        
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
            
        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")
            
        return user, self.get_tokens_for_user(user)
