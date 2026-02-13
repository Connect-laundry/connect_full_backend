# pyre-ignore[missing-module]
from rest_framework import serializers
from ..models.review import Review

class ReviewSerializer(serializers.ModelSerializer):
    userName = serializers.CharField(source='user.first_name', read_only=True)
    date = serializers.DateTimeField(source='created_at', format='%Y-%m-%dT%H:%M:%S', read_only=True)

    class Meta:
        model = Review
        fields = ('id', 'userName', 'rating', 'comment', 'date')

    def create(self, validated_data):
        user = self.context['request'].user
        return Review.objects.create(user=user, **validated_data)
