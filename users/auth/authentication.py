from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from users.services.clerk_service import authenticate_clerk_token


class ClerkOrJWTAuthentication(JWTAuthentication):
    """Accept existing Connect JWTs and Clerk session JWTs.

    Existing internal tokens stay primary for compatibility. If a bearer token is
    not a valid Connect JWT, it is treated as a Clerk session token and resolved
    to the synchronized local Django user.
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        simplejwt_error = None
        try:
            validated_token = self.get_validated_token(raw_token)
            return self.get_user(validated_token), validated_token
        except Exception as exc:
            simplejwt_error = exc

        try:
            user, _created = authenticate_clerk_token(raw_token.decode('utf-8'), request=request)
            return user, {'provider': 'clerk'}
        except AuthenticationFailed:
            raise
        except Exception as exc:
            raise AuthenticationFailed('Invalid authentication token.') from (simplejwt_error or exc)
