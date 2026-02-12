# pyre-ignore[missing-module]
from django.contrib.auth.password_validation import validate_password
# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from rest_framework.validators import UniqueValidator
# pyre-ignore[missing-module]
from ..models import User

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    password_confirm = serializers.CharField(write_only=True, required=True)
    
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )
    phone = serializers.CharField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )

    class Meta:
        model = User
        fields = (
            'email', 'phone', 'first_name', 'last_name', 
            'password', 'password_confirm'
        )

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        return attrs
