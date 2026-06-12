import os

from django.conf import settings # type: ignore
from django.core.checks import Error, Tags, register # type: ignore


@register(Tags.security, deploy=True)
def clerk_production_configuration_check(app_configs, **kwargs):
    if getattr(settings, 'DEBUG', False):
        return []

    required = {
        'CLERK_APPLICATION_ID': os.getenv('CLERK_APPLICATION_ID'),
        'CLERK_PUBLISHABLE_KEY': os.getenv('CLERK_PUBLISHABLE_KEY'),
        'CLERK_SECRET_KEY': os.getenv('CLERK_SECRET_KEY'),
        'CLERK_JWKS_URL': os.getenv('CLERK_JWKS_URL'),
        'CLERK_JWT_AUDIENCE': os.getenv('CLERK_JWT_AUDIENCE'),
        'CLERK_WEBHOOK_SECRET': os.getenv('CLERK_WEBHOOK_SECRET'),
    }
    issuer = os.getenv('CLERK_JWT_ISSUER') or os.getenv('CLERK_ISSUER')
    if issuer:
        required['CLERK_JWT_ISSUER or CLERK_ISSUER'] = issuer

    missing = [name for name, value in required.items() if not value]
    errors = [
        Error(
            f'{name} must be configured for production Clerk integration.',
            id='users.E_CLERK_CONFIG',
        )
        for name in missing
    ]

    if not issuer:
        errors.append(
            Error(
                'CLERK_JWT_ISSUER or CLERK_ISSUER must be configured for Clerk JWT issuer validation.',
                id='users.E_CLERK_CONFIG',
            )
        )

    webhook_secret = os.getenv('CLERK_WEBHOOK_SECRET', '')
    if webhook_secret and not webhook_secret.startswith('whsec_'):
        errors.append(
            Error(
                'CLERK_WEBHOOK_SECRET must be the Clerk/Svix signing secret value.',
                id='users.E_CLERK_WEBHOOK_SECRET',
            )
        )

    return errors
