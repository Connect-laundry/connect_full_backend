from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from users.serializers.profile import ProfileSerializer
from users.serializers.social import SocialLoginSerializer, SocialSessionSerializer
from users.services.auth_service import AuthService
from users.services.clerk_service import authenticate_clerk_token


def _auth_payload(user, tokens):
    return {
        'accessToken': tokens['access'],
        'refreshToken': tokens['refresh'],
        'user': {
            'id': str(user.id),
            'email': user.email,
            'fullName': user.get_full_name(),
            'role': user.role,
        },
    }


class SocialLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = SocialLoginSerializer

    @extend_schema(request=SocialLoginSerializer)
    def post(self, request):
        serializer = SocialLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user, _created = authenticate_clerk_token(
            serializer.validated_data['clerk_token'],
            requested_role=serializer.validated_data.get('role'),
            request=request,
        )
        if not user.is_active:
            return Response({'detail': 'User account is disabled.'}, status=status.HTTP_403_FORBIDDEN)

        tokens = AuthService.get_tokens_for_user(user, request)
        return Response(_auth_payload(user, tokens), status=status.HTTP_200_OK)


class SessionView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SocialSessionSerializer

    @extend_schema(request=None, responses=SocialSessionSerializer)
    def get(self, request):
        serializer = ProfileSerializer(request.user, context={'request': request})
        return Response({
            'authenticated': True,
            'user': serializer.data,
        }, status=status.HTTP_200_OK)
