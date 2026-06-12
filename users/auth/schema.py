from drf_spectacular.extensions import OpenApiAuthenticationExtension # type: ignore


class ClerkOrJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'users.auth.authentication.ClerkOrJWTAuthentication'
    name = 'BearerAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
            'description': 'Connect SimpleJWT access token or verified Clerk session JWT.',
        }
