import jwt  # type: ignore
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from users.services.clerk_service import authenticate_clerk_token

# Connect SimpleJWT tokens are signed with HS256; Clerk session tokens use RS256.
# We only attempt the Clerk fallback when a token is NOT an HS256 Connect token,
# so a plain expired/invalid Connect token surfaces its real SimpleJWT error
# instead of the misleading "Invalid Clerk session token." message.
_CONNECT_JWT_ALGORITHM = 'HS256'


def _looks_like_connect_jwt(raw_token: bytes) -> bool:
    """Return True if the token's unverified header marks it as a Connect HS256 JWT.

    Returns False for Clerk (RS256) tokens and for anything that isn't a parseable
    JWT, so those continue to the Clerk fallback path.
    """
    try:
        header = jwt.get_unverified_header(raw_token)
    except jwt.PyJWTError:
        return False
    return header.get('alg') == _CONNECT_JWT_ALGORITHM


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

        try:
            validated_token = self.get_validated_token(raw_token)
            return self.get_user(validated_token), validated_token
        except Exception as simplejwt_error:
            # A token that identifies itself as a Connect HS256 JWT but failed
            # validation is simply an invalid/expired Connect token — re-raise the
            # real SimpleJWT error rather than masking it as a Clerk failure.
            if _looks_like_connect_jwt(raw_token):
                raise

        try:
            user, _created = authenticate_clerk_token(raw_token.decode('utf-8'), request=request)
            return user, {'provider': 'clerk'}
        except AuthenticationFailed:
            raise
        except Exception as exc:
            raise AuthenticationFailed('Invalid authentication token.') from exc
