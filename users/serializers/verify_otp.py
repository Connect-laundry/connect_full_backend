from rest_framework import serializers

class VerifyOTPSerializer(serializers.Serializer):
    identifier = serializers.CharField(required=True, help_text="Email or Phone number")
    otp = serializers.CharField(required=True, min_length=6, max_length=6)
