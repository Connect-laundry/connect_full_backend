import logging
from dataclasses import dataclass
from datetime import timezone as dt_timezone
from functools import lru_cache
from typing import Any

import jwt # type: ignore
import requests # type: ignore
from django.conf import settings # type: ignore
from django.db import transaction # type: ignore
from django.utils.dateparse import parse_datetime # type: ignore
from django.utils import timezone # type: ignore
from rest_framework.exceptions import AuthenticationFailed, ValidationError # type: ignore

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
    email_verified: bool = True
    phone_verified: bool = False
    last_sign_in_at: Any = None
    clerk_created_at: Any = None
    clerk_updated_at: Any = None
    status: str = 'active'
    metadata: dict[str, Any] | None = None


class ClerkTokenVerifier:
    def __init__(self):
        self.issuer = getattr(settings, 'CLERK_ISSUER', '')
        self.audience = getattr(settings, 'CLERK_JWT_AUDIENCE', '')
        self.jwks_url = getattr(settings, 'CLERK_JWKS_URL', '')
        self.leeway = getattr(settings, 'CLERK_JWT_LEEWAY_SECONDS', 30)
        self.jwks_cache_seconds = getattr(settings, 'CLERK_JWKS_CACHE_SECONDS', 300)

    def verify(self, token: str) -> dict[str, Any]:
        if not self.issuer:
            raise AuthenticationFailed('Clerk authentication is not configured.')

        jwks_url = self.jwks_url or f'{self.issuer.rstrip("/")}/.well-known/jwks.json'
        try:
            signing_key = _jwks_client(jwks_url, self.jwks_cache_seconds).get_signing_key_from_jwt(token).key
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
            logger.warning('Clerk JWKS verification failed', extra={'error_type': type(exc).__name__})
            raise AuthenticationFailed('Unable to verify Clerk session token.') from exc

        if not payload.get('sub'):
            raise AuthenticationFailed('Clerk session token is missing the user id.')
        return payload


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str, cache_seconds: int):
    return jwt.PyJWKClient(
        jwks_url,
        cache_jwk_set=True,
        lifespan=max(60, int(cache_seconds or 300)),
    )


def _is_verified_email(item: dict[str, Any]) -> bool:
    verification = item.get('verification') or {}
    return (
        item.get('verified') is True
        or item.get('email_verified') is True
        or verification.get('status') == 'verified'
    )


def _is_verified_phone(item: dict[str, Any]) -> bool:
    verification = item.get('verification') or {}
    return (
        item.get('verified') is True
        or item.get('phone_verified') is True
        or verification.get('status') == 'verified'
    )


def _claim_email_verified(payload: dict[str, Any]) -> bool:
    value = payload.get('email_verified') or payload.get('email_verified_at')
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'false', '0', 'no', ''}:
            return False
        if normalized in {'true', '1', 'yes'}:
            return True
        return True
    return bool(value)


def _parse_clerk_datetime(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        # Clerk API timestamps are commonly Unix milliseconds.
        seconds = value / 1000 if value > 10_000_000_000 else value
        return timezone.datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone.utc)
        return parsed
    return None


def _primary_email(data: dict[str, Any]) -> tuple[str, bool]:
    primary_email_id = data.get('primary_email_address_id')
    for item in data.get('email_addresses') or []:
        if item.get('id') == primary_email_id and item.get('email_address'):
            return item['email_address'], _is_verified_email(item)
    for item in data.get('email_addresses') or []:
        if item.get('email_address'):
            return item['email_address'], _is_verified_email(item)
    return data.get('email') or data.get('email_address') or '', bool(data.get('email_verified'))


def _phone_verified(data: dict[str, Any]) -> bool:
    primary_phone_id = data.get('primary_phone_number_id')
    for item in data.get('phone_numbers') or []:
        if primary_phone_id and item.get('id') != primary_phone_id:
            continue
        return _is_verified_phone(item)
    return False


def _provider_from_profile(data: dict[str, Any], payload: dict[str, Any]) -> str:
    for item in data.get('external_accounts') or []:
        provider = item.get('provider') or item.get('strategy')
        if provider:
            return provider
    return payload.get('social_provider') or payload.get('provider') or ''


def _status_from_profile(data: dict[str, Any]) -> str:
    if data.get('deleted') is True:
        return 'deleted'
    if data.get('banned') is True:
        return 'banned'
    if data.get('locked') is True:
        return 'locked'
    return 'active'


def _metadata_from_profile(data: dict[str, Any]) -> dict[str, Any]:
    return {
        'public_metadata': data.get('public_metadata') or {},
        'unsafe_metadata': data.get('unsafe_metadata') or {},
        'external_accounts': [
            {
                'provider': item.get('provider') or item.get('strategy') or '',
                'id': item.get('id') or '',
            }
            for item in data.get('external_accounts') or []
        ],
    }


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
        email_verified=_claim_email_verified(payload),
        phone_verified=bool(payload.get('phone_verified')),
        last_sign_in_at=_parse_clerk_datetime(payload.get('last_sign_in_at')),
        clerk_created_at=_parse_clerk_datetime(payload.get('created_at')),
        clerk_updated_at=_parse_clerk_datetime(payload.get('updated_at')),
        metadata={},
    )


def profile_from_clerk_user_data(data: dict[str, Any], payload: dict[str, Any] | None = None) -> ClerkProfile:
    payload = payload or {}
    email, email_verified = _primary_email(data)
    return ClerkProfile(
        clerk_user_id=data['id'],
        email=email,
        first_name=data.get('first_name') or '',
        last_name=data.get('last_name') or '',
        image_url=data.get('image_url') or data.get('profile_image_url') or '',
        provider=_provider_from_profile(data, payload),
        email_verified=email_verified,
        phone_verified=_phone_verified(data),
        last_sign_in_at=_parse_clerk_datetime(data.get('last_sign_in_at')),
        clerk_created_at=_parse_clerk_datetime(data.get('created_at')),
        clerk_updated_at=_parse_clerk_datetime(data.get('updated_at')),
        status=_status_from_profile(data),
        metadata=_metadata_from_profile(data),
    )


def fetch_clerk_profile(payload: dict[str, Any]) -> ClerkProfile:
    secret_key = getattr(settings, 'CLERK_SECRET_KEY', '')
    if not secret_key:
        return _profile_from_claims(payload)

    api_base_url = getattr(settings, 'CLERK_API_BASE_URL', 'https://api.clerk.com').rstrip('/')
    timeout = getattr(settings, 'CLERK_API_TIMEOUT_SECONDS', 5)
    url = f'{api_base_url}/v1/users/{payload["sub"]}'
    try:
        response = requests.get(
            url,
            headers={'Authorization': f'Bearer {secret_key}'},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        logger.warning('Clerk profile lookup failed; using token claims', extra={'error_type': type(exc).__name__})
        return _profile_from_claims(payload)

    return profile_from_clerk_user_data(data, payload)


def fetch_clerk_profile_by_user_id(clerk_user_id: str) -> ClerkProfile:
    secret_key = getattr(settings, 'CLERK_SECRET_KEY', '')
    if not secret_key:
        raise ValidationError({'clerk': ['CLERK_SECRET_KEY is required to resync Clerk users.']})

    api_base_url = getattr(settings, 'CLERK_API_BASE_URL', 'https://api.clerk.com').rstrip('/')
    timeout = getattr(settings, 'CLERK_API_TIMEOUT_SECONDS', 5)
    url = f'{api_base_url}/v1/users/{clerk_user_id}'
    try:
        response = requests.get(
            url,
            headers={'Authorization': f'Bearer {secret_key}'},
            timeout=timeout,
        )
        response.raise_for_status()
        return profile_from_clerk_user_data(response.json())
    except requests.RequestException as exc:
        logger.warning('Clerk profile resync failed', extra={'error_type': type(exc).__name__})
        raise ValidationError({'clerk': ['Unable to fetch Clerk user profile.']}) from exc


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
    source: str = 'auth',
    require_verified_email: bool = True,
) -> tuple[User, bool]:
    if not profile.email or (require_verified_email and not profile.email_verified):
        raise ValidationError({'email': ['Clerk did not provide a verified email address.']})

    provider = normalize_provider(profile.provider)
    if provider and provider not in ALLOWED_SOCIAL_PROVIDERS:
        raise ValidationError({'provider': ['Only Google and Facebook sign-in are supported.']})

    if requested_role and requested_role not in ALLOWED_SOCIAL_ROLES:
        raise ValidationError({'role': ['Only CUSTOMER and OWNER can be requested during social sign-in.']})

    email = User.objects.normalize_email(profile.email).strip().lower() # type: ignore
    with transaction.atomic():
        user = User.objects.select_for_update().filter(clerk_user_id=profile.clerk_user_id).first()
        created = False
        if user is None:
            if not profile.email_verified:
                raise ValidationError({'email': ['Clerk did not provide a verified email address.']})
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
            'auth_provider': provider or user.auth_provider,
            'primary_email': email,
            'email': email,
            'first_name': profile.first_name or user.first_name,
            'last_name': profile.last_name or user.last_name,
            'social_provider': provider or user.social_provider,
            'social_profile_image_url': profile.image_url or user.social_profile_image_url,
            'email_verified': profile.email_verified,
            'phone_verified': profile.phone_verified,
            'last_social_login_at': now if source == 'auth' else user.last_social_login_at,
            'last_clerk_sign_in_at': profile.last_sign_in_at or (now if source == 'auth' else user.last_clerk_sign_in_at),
            'last_clerk_sync': now,
            'clerk_created_at': profile.clerk_created_at or user.clerk_created_at,
            'clerk_updated_at': profile.clerk_updated_at or user.clerk_updated_at,
            'clerk_status': profile.status or user.clerk_status or 'active',
            'clerk_metadata': profile.metadata or user.clerk_metadata,
            'is_verified': profile.email_verified,
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
        target_id=user.id, # type: ignore
        target_repr=user.email,
        metadata={
            'event': 'social_sign_in',
            'source': source,
            'provider': provider,
            'created': created,
            'clerk_user_id': profile.clerk_user_id,
        },
    )
    return user, created


def deactivate_user_from_clerk(clerk_user_id: str, *, request=None, reason: str = 'clerk_user_deleted') -> User | None:
    with transaction.atomic():
        user = User.objects.select_for_update().filter(clerk_user_id=clerk_user_id).first()
        if user is None:
            return None
        now = timezone.now()
        changed = ['is_active', 'clerk_status', 'last_clerk_sync', 'deactivated_at', 'deactivation_reason']
        user.is_active = False
        user.clerk_status = 'deleted'
        user.last_clerk_sync = now
        if user.deactivated_at is None:
            user.deactivated_at = now
        user.deactivation_reason = reason
        user.save(update_fields=changed)

    record_audit(
        action=AuditLog.Action.SECURITY_EVENT,
        actor=user,
        request=request,
        target_type='User',
        target_id=user.id, # type: ignore
        target_repr=user.email,
        metadata={'event': 'clerk_user_deleted', 'clerk_user_id': clerk_user_id},
    )
    return user


def authenticate_clerk_token(token: str, *, requested_role: str | None = None, request=None):
    payload = ClerkTokenVerifier().verify(token)
    profile = fetch_clerk_profile(payload)
    return sync_user_from_clerk(profile=profile, requested_role=requested_role, request=request)
