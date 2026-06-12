import logging
from dataclasses import dataclass
from typing import Any

import jwt
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, ValidationError

from marketplace.models import AuditLog
from marketplace.services.audit import record_audit
from users.models import User

logger = logging.getLogger(__name__)

ALLOWED_SOCIAL_ROLES = {User.Role.CUSTOMER, User.Role.OWNER}
ALLOWED_SOCIAL_PROVIDERS = {'oauth_google', 'oauth_facebook', 'google', 'facebook'}


@dataclass(frozen=True)
class ClerkProfile:
    clerk_user_id: str
    email: str
    first_name: str = ''
    last_name: str = ''
    image_url: str = ''
    provider: str = ''


class ClerkTokenVerifier:
    def __init__(self):
        self.issuer = getattr(settings, 'CLERK_ISSUER', '')
        self.audience = getattr(settings, 'CLERK_JWT_AUDIENCE', '')
        self.jwks_url = getattr(settings, 'CLERK_JWKS_URL', '')
        self.leeway = getattr(settings, 'CLERK_JWT_LEEWAY_SECONDS', 30)

    def verify(self, token: str) -> dict[str, Any]:
        if not self.issuer:
            raise AuthenticationFailed('Clerk authentication is not configured.')

        jwks_url = self.jwks_url or f'{self.issuer.rstrip("/")}/.well-known/jwks.json'
        try:
            signing_key = jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key
            decode_kwargs: dict[str, Any] = {
                'key': signing_key,
                'algorithms': ['RS256'],
                'issuer': self.issuer,
                'leeway': self.leeway,
                'options': {
                    'require': ['exp', 'iat', 'iss', 'sub'],
                    'verify_aud': bool(self.audience),
                },
            }
            if self.audience:
                decode_kwargs['audience'] = self.audience
            payload = jwt.decode(token, **decode_kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationFailed('Clerk session token has expired.') from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationFailed('Invalid Clerk session token.') from exc
        except Exception as exc:
            logger.warning('Clerk JWKS verification failed', extra={'error': str(exc)})
            raise AuthenticationFailed('Unable to verify Clerk session token.') from exc

        if not payload.get('sub'):
            raise AuthenticationFailed('Clerk session token is missing the user id.')
        return payload


def _primary_email(data: dict[str, Any]) -> str:
    primary_email_id = data.get('primary_email_address_id')
    for item in data.get('email_addresses') or []:
        if item.get('id') == primary_email_id and item.get('email_address'):
            return item['email_address']
    for item in data.get('email_addresses') or []:
        if item.get('email_address'):
            return item['email_address']
    return data.get('email') or data.get('email_address') or ''


def _provider_from_profile(data: dict[str, Any], payload: dict[str, Any]) -> str:
    for item in data.get('external_accounts') or []:
        provider = item.get('provider') or item.get('strategy')
        if provider:
            return provider
    return payload.get('social_provider') or payload.get('provider') or ''


def _profile_from_claims(payload: dict[str, Any]) -> ClerkProfile:
    email = payload.get('email') or payload.get('email_address') or ''
    name = (payload.get('name') or '').strip()
    first_name = payload.get('given_name') or payload.get('first_name') or ''
    last_name = payload.get('family_name') or payload.get('last_name') or ''
    if name and not (first_name or last_name):
        parts = name.split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''
    return ClerkProfile(
        clerk_user_id=payload['sub'],
        email=email,
        first_name=first_name,
        last_name=last_name,
        image_url=payload.get('picture') or payload.get('image_url') or '',
        provider=payload.get('social_provider') or payload.get('provider') or '',
    )


def fetch_clerk_profile(payload: dict[str, Any]) -> ClerkProfile:
    secret_key = getattr(settings, 'CLERK_SECRET_KEY', '')
    if not secret_key:
        return _profile_from_claims(payload)

    api_base_url = getattr(settings, 'CLERK_API_BASE_URL', 'https://api.clerk.com').rstrip('/')
    url = f'{api_base_url}/v1/users/{payload["sub"]}'
    try:
        response = requests.get(
            url,
            headers={'Authorization': f'Bearer {secret_key}'},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.warning('Clerk profile lookup failed; using token claims', extra={'error': str(exc)})
        return _profile_from_claims(payload)

    return ClerkProfile(
        clerk_user_id=data['id'],
        email=_primary_email(data),
        first_name=data.get('first_name') or '',
        last_name=data.get('last_name') or '',
        image_url=data.get('image_url') or data.get('profile_image_url') or '',
        provider=_provider_from_profile(data, payload),
    )


def normalize_provider(provider: str) -> str:
    provider = (provider or '').strip().lower()
    aliases = {
        'google': 'oauth_google',
        'facebook': 'oauth_facebook',
    }
    return aliases.get(provider, provider)


def sync_user_from_clerk(
    *,
    profile: ClerkProfile,
    requested_role: str | None = None,
    request=None,
) -> tuple[User, bool]:
    if not profile.email:
        raise ValidationError({'email': ['Clerk did not provide a verified email address.']})

    provider = normalize_provider(profile.provider)
    if provider and provider not in ALLOWED_SOCIAL_PROVIDERS:
        raise ValidationError({'provider': ['Only Google and Facebook sign-in are supported.']})

    if requested_role and requested_role not in ALLOWED_SOCIAL_ROLES:
        raise ValidationError({'role': ['Only CUSTOMER and OWNER can be requested during social sign-in.']})

    email = User.objects.normalize_email(profile.email)
    with transaction.atomic():
        user = User.objects.select_for_update().filter(clerk_user_id=profile.clerk_user_id).first()
        created = False
        if user is None:
            user = User.objects.select_for_update().filter(email__iexact=email).first()
            if user and user.clerk_user_id and user.clerk_user_id != profile.clerk_user_id:
                raise ValidationError({'email': ['This email is already linked to a different Clerk account.']})
        elif user.email.lower() != email.lower():
            conflicting_user = User.objects.select_for_update().filter(email__iexact=email).exclude(id=user.id).first()
            if conflicting_user:
                raise ValidationError({'email': ['This email is already used by another account.']})

        now = timezone.now()
        if user is None:
            user = User(
                email=email,
                role=requested_role if requested_role in ALLOWED_SOCIAL_ROLES else User.Role.CUSTOMER,
                is_verified=True,
            )
            user.set_unusable_password()
            created = True

        update_fields = []
        field_values = {
            'clerk_user_id': profile.clerk_user_id,
            'email': email,
            'first_name': profile.first_name or user.first_name,
            'last_name': profile.last_name or user.last_name,
            'social_provider': provider or user.social_provider,
            'social_profile_image_url': profile.image_url or user.social_profile_image_url,
            'last_social_login_at': now,
            'is_verified': True,
        }
        for field, value in field_values.items():
            if getattr(user, field) != value:
                setattr(user, field, value)
                update_fields.append(field)

        if created:
            user.save()
        elif update_fields:
            update_fields.append('updated_at')
            user.save(update_fields=update_fields)

    record_audit(
        action=AuditLog.Action.SECURITY_EVENT,
        actor=user,
        request=request,
        target_type='User',
        target_id=user.id,
        target_repr=user.email,
        metadata={
            'event': 'social_sign_in',
            'provider': provider,
            'created': created,
            'clerk_user_id': profile.clerk_user_id,
        },
    )
    return user, created


def authenticate_clerk_token(token: str, *, requested_role: str | None = None, request=None):
    payload = ClerkTokenVerifier().verify(token)
    profile = fetch_clerk_profile(payload)
    return sync_user_from_clerk(profile=profile, requested_role=requested_role, request=request)
