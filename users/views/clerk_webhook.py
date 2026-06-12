import logging

from rest_framework import permissions, status # type: ignore
from rest_framework.exceptions import AuthenticationFailed, ValidationError # type: ignore
from rest_framework.response import Response # type: ignore
from rest_framework import serializers # type: ignore
from rest_framework.views import APIView # type: ignore
from drf_spectacular.utils import extend_schema # type: ignore

from users.services.clerk_webhook_service import process_clerk_webhook

logger = logging.getLogger(__name__)


class ClerkWebhookRequestSerializer(serializers.Serializer):
    type = serializers.CharField()
    data = serializers.DictField()


class ClerkWebhookResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    event_type = serializers.CharField(allow_blank=True, required=False)


class ClerkWebhookView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        request=ClerkWebhookRequestSerializer,
        responses={200: ClerkWebhookResponseSerializer, 202: ClerkWebhookResponseSerializer},
        auth=[],
    )
    def post(self, request):
        try:
            result = process_clerk_webhook(
                body=request.body,
                headers=dict(request.headers),
            )
        except AuthenticationFailed as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        except ValidationError as exc:
            return Response({'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.warning('Clerk webhook rejected during processing', extra={'error_type': type(exc).__name__})
            return Response({'detail': 'Webhook could not be processed.'}, status=status.HTTP_400_BAD_REQUEST)

        http_status = status.HTTP_202_ACCEPTED if result['status'] == 'ignored' else status.HTTP_200_OK
        return Response(result, status=http_status)
