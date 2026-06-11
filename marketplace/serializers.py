# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from .models import Notification, Feedback, PushDevice, LegalPage, UserLegalAcceptance

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id', 'title', 'body', 'type', 'audience', 'category',
            'priority', 'action_url', 'is_read', 'created_at', 'read_at',
            'related_order',
        ]
        read_only_fields = ['id', 'created_at', 'read_at']

class FeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feedback
        fields = ['id', 'subject', 'message', 'created_at']
        read_only_fields = ['id', 'created_at']


class PushDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushDevice
        fields = [
            'id', 'token', 'device_id', 'platform', 'app_version', 
            'is_active', 'last_registered_at', 'web_endpoint', 
            'web_p256dh', 'web_auth'
        ]
        read_only_fields = ['id', 'is_active', 'last_registered_at']


class WebPushSubscriptionKeysSerializer(serializers.Serializer):
    p256dh = serializers.CharField(max_length=255)
    auth = serializers.CharField(max_length=255)


class WebPushSubscriptionSerializer(serializers.Serializer):
    endpoint = serializers.CharField()
    keys = WebPushSubscriptionKeysSerializer()


class LegalPageSerializer(serializers.ModelSerializer):
    public_url = serializers.CharField(source='public_path', read_only=True)

    class Meta:
        model = LegalPage
        fields = [
            'id', 'title', 'slug', 'document_type', 'short_description',
            'content_markdown', 'content_html', 'summary', 'version_number',
            'effective_date', 'published_at', 'requires_user_reacceptance',
            'is_active', 'is_published', 'is_public', 'seo_title',
            'seo_description', 'previous_version', 'change_log', 'tags',
            'language_code', 'sort_order', 'public_url', 'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'content_html', 'published_at', 'previous_version',
            'public_url', 'created_at', 'updated_at',
        ]


class LegalPagePublicSerializer(serializers.ModelSerializer):
    public_url = serializers.CharField(source='public_path', read_only=True)

    class Meta:
        model = LegalPage
        fields = [
            'id', 'title', 'slug', 'document_type', 'short_description',
            'content_html', 'summary', 'version_number', 'effective_date',
            'published_at', 'requires_user_reacceptance', 'seo_title',
            'seo_description', 'tags', 'language_code', 'sort_order',
            'public_url', 'updated_at',
        ]


class LegalPageSummarySerializer(serializers.ModelSerializer):
    public_url = serializers.CharField(source='public_path', read_only=True)

    class Meta:
        model = LegalPage
        fields = [
            'id', 'title', 'slug', 'document_type', 'short_description',
            'summary', 'version_number', 'effective_date', 'published_at',
            'requires_user_reacceptance', 'language_code', 'public_url',
            'updated_at',
        ]


class LegalAcceptanceSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(source='legal_page.slug', read_only=True)
    title = serializers.CharField(source='legal_page.title', read_only=True)

    class Meta:
        model = UserLegalAcceptance
        fields = [
            'id', 'legal_page', 'slug', 'title', 'accepted_version',
            'accepted_at', 'platform', 'app_version',
        ]
        read_only_fields = ['id', 'slug', 'title', 'accepted_version', 'accepted_at']
