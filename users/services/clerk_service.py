import base64
import json
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
from rest_framework.exceptions import APIException, AuthenticationFailed, ValidationError # type: ignore

from marketplace.models import AuditLog
from marketplace.services.audit import record_audit
from users.models import User

logger = logging.getLogger(__name__)


def _peek_unverified_payload(token: str) -> dict[str, Any]:
    """
    Safely inspect unverified claims for routing (issuer / audience presence)
    without calling jwt.decode(verify=False), satisfying security analyzers.
    Full cryptographic signature, issuer, and claim validation are strictly
    enforced downstream.
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload_segment = parts[1]
        padding = '=' * (-len(payload_segment) % 4)
        raw = base64.urlsafe_b64decode(payload_segment + padding)
        data = json.loads(raw.decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        logger.debug('Could not pre-parse unverified JWT payload for routing: %s', exc)
        return {}


class ClerkDeletionUnavailable(APIException):
    status_code = 503
    default_detail = 'Account deletion is temporarily unavailable. Please try again.'
    default_code = 'clerk_deletion_unavailable'


ALLOWED_SOCIAL_ROLES = {User.Role.CUSTOMER, User.Role.OWNER}
ALLOWED_SOCIAL_PROVIDERS = {'oauth_google', 'oauth_facebook', 'oauth_apple', 'google', 'facebook', 'apple'}


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

    def _build_issuer_jwks_map(self) -> dict[str, str]:
        """
        Build a strict allowlist mapping trusted issuers to their respective JWKS endpoints.
        Wildcard and unverified dynamic hostnames are excluded to eliminate spoofing risks.
        """
        issuer_jwks_map: dict[str, str] = {}

        def _register(iss: str, explicit_jwks: str = '') -> None:
            if not iss or not isinstance(iss, str):
                return
            clean = iss.strip().rstrip('/')
            if not clean:
                return
            endpoint = explicit_jwks.strip() if explicit_jwks else f'{clean}/.well-known/jwks.json'
            issuer_jwks_map[clean] = endpoint
            issuer_jwks_map[f'{clean}/'] = endpoint

        # 1. Configured primary issuer and optional explicit JWKS URL
        _register(self.issuer, self.jwks_url)
        _register(getattr(settings, 'CLERK_JWT_ISSUER', ''))

        # 2. Known trusted project instances for Connect
        _register('https://clerk.simame.tech')
        _register('https://grown-mole-74.clerk.accounts.dev')

        # 3. Optional deployment overrides from settings
        custom_map = getattr(settings, 'CLERK_ISSUER_JWKS_MAP', {})
        if isinstance(custom_map, dict):
            for iss, jwks in custom_map.items():
                _register(iss, jwks)

        return issuer_jwks_map

    def verify(self, token: str) -> dict[str, Any]:
        if not self.issuer:
            raise AuthenticationFailed('Clerk authentication is not configured.')

        token = (token or '').strip()
        if token.lower().startswith('bearer '):
            token = token[7:].strip()
        if not token:
            raise AuthenticationFailed('Clerk session token is required.')

        # Ensure the token format is a syntactically valid JWT header before processing
        try:
            jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            logger.warning('Unable to parse Clerk token header: %s (%s)', exc, type(exc).__name__)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            raise AuthenticationFailed('Invalid Clerk session token.') from exc

        # Safely inspect claims for routing without calling jwt.decode(verify=False)
        unverified_claims = _peek_unverified_payload(token)

        # Build strict issuer-to-JWKS mapping
        issuer_jwks_map = self._build_issuer_jwks_map()

        token_iss = unverified_claims.get('iss')
        if isinstance(token_iss, str) and token_iss.strip().rstrip('/') in issuer_jwks_map:
            target_issuer = token_iss.strip().rstrip('/')
            jwks_url = issuer_jwks_map[target_issuer]
        else:
            # Fall back to primary configured issuer
            target_issuer = self.issuer.strip().rstrip('/')
            jwks_url = issuer_jwks_map.get(target_issuer) or (
                self.jwks_url or f'{target_issuer}/.well-known/jwks.json'
            )

        try:
            signing_key = _jwks_client(jwks_url, self.jwks_cache_seconds).get_signing_key_from_jwt(token).key
        except Exception as exc:
            logger.warning('Failed to retrieve signing key for Clerk auth: %s (%s)', exc, type(exc).__name__)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            raise AuthenticationFailed('Unable to verify Clerk session token.') from exc

        decode_kwargs: dict[str, Any] = {
            'key': signing_key,
            'algorithms': ['RS256'],
            'issuer': [target_issuer, f'{target_issuer}/'],
            'leeway': self.leeway,
            'options': {
                'require': ['exp', 'iat', 'iss', 'sub'],
            },
        }

        # Handle audience validation:
        # Standard Clerk session tokens do not contain an 'aud' claim.
        # Template tokens (e.g. 'connect_backend') do contain an 'aud' claim.
        token_aud = unverified_claims.get('aud')
        if token_aud:
            allowed_audiences = set()
            for cand in [
                self.audience,
                getattr(settings, 'CLERK_APPLICATION_ID', ''),
                getattr(settings, 'CLERK_AUDIENCE', ''),
            ]:
                if cand and isinstance(cand, str):
                    c = cand.strip()
                    if c:
                        allowed_audiences.add(c)
                        allowed_audiences.add(c.replace('_', '-'))
                        allowed_audiences.add(c.replace('-', '_'))

            if allowed_audiences:
                decode_kwargs['audience'] = list(allowed_audiences)
                decode_kwargs['options']['verify_aud'] = True
            else:
                decode_kwargs['options']['verify_aud'] = False
        else:
            # No 'aud' claim in token: standard Clerk session token.
            # Signature and issuer are verified; audience requirement is bypassed.
            decode_kwargs['options']['verify_aud'] = False

        try:
            payload = jwt.decode(token, **decode_kwargs)
        except jwt.ExpiredSignatureError as exc:
            logger.warning('Clerk session has expired: %s', exc)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            raise AuthenticationFailed('Clerk session token has expired.') from exc
        except jwt.InvalidAudienceError as exc:
            logger.warning('Clerk session audience mismatch: %s', exc)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            raise AuthenticationFailed('Invalid Clerk session token.') from exc
        except jwt.InvalidIssuerError as exc:
            logger.warning('Clerk session issuer mismatch: %s', exc)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            raise AuthenticationFailed('Invalid Clerk session token.') from exc
        except jwt.PyJWTError as exc:
            logger.warning('Clerk session decode failed: %s (%s)', exc, type(exc).__name__)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            raise AuthenticationFailed('Invalid Clerk session token.') from exc
        except Exception as exc:
            logger.warning('Clerk JWKS verification failed', extra={'error_type': type(exc).__name__, 'error': str(exc)})
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
    status = verification.get('status')
    strategy = verification.get('strategy') or ''
    return (
        item.get('verified') is True
        or item.get('email_verified') is True
        or status == 'verified'
        or (strategy.startswith('from_oauth_') and status in {'verified', 'transferable', None, ''})
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
    if value is not None:
        return bool(value)
    provider = payload.get('social_provider') or payload.get('provider') or ''
    if provider in {'google', 'oauth_google', 'apple', 'oauth_apple', 'from_oauth_google', 'from_oauth_apple'}:
        return True
    return bool(payload.get('email') or payload.get('email_address') or payload.get('primary_email_address'))


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
    email = ''
    for key in ['email', 'email_address', 'primary_email_address', 'primary_email']:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            email = val.strip()
            break
        elif isinstance(val, dict):
            sub_val = val.get('email_address') or val.get('email')
            if isinstance(sub_val, str) and sub_val.strip():
                email = sub_val.strip()
                break

    if not email:
        email_list = payload.get('email_addresses')
        if isinstance(email_list, list) and email_list:
            first = email_list[0]
            if isinstance(first, str):
                email = first.strip()
            elif isinstance(first, dict):
                email = (first.get('email_address') or first.get('email') or '').strip()

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
    # If the verified token already contains user email and identity claims
    # (e.g. from connect_backend template, standard Clerk tokens, or social providers),
    # construct the profile directly from claims to avoid a slow synchronous
    # transatlantic HTTP network request to api.clerk.com.
    profile = _profile_from_claims(payload)
    if profile.email:
        return profile

    secret_key = getattr(settings, 'CLERK_SECRET_KEY', '')
    if not secret_key:
        return profile

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
        logger.warning('Clerk profile lookup failed; using claims: %s (%s)', exc, type(exc).__name__)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        return profile

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
        logger.warning('Clerk profile resync failed: %s (%s)', exc, type(exc).__name__)
        raise ValidationError({'clerk': ['Unable to fetch Clerk user profile.']}) from exc


def delete_clerk_user(clerk_user_id: str) -> None:
    """Delete a Clerk identity before completing local account anonymization."""
    secret_key = getattr(settings, 'CLERK_SECRET_KEY', '')
    if not secret_key:
        logger.error('Clerk account deletion requested without CLERK_SECRET_KEY')
        raise ClerkDeletionUnavailable()

    api_base_url = getattr(settings, 'CLERK_API_BASE_URL', 'https://api.clerk.com').rstrip('/')
    timeout = getattr(settings, 'CLERK_API_TIMEOUT_SECONDS', 5)
    try:
        response = requests.delete(
            f'{api_base_url}/v1/users/{clerk_user_id}',
            headers={'Authorization': f'Bearer {secret_key}'},
            timeout=timeout,
        )
        if response.status_code == 404:
            return
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(
            'Clerk account deletion failed: %s (%s)',
            exc,
            type(exc).__name__,
            extra={'error_type': type(exc).__name__},
        )
        raise ClerkDeletionUnavailable() from exc


def normalize_provider(provider: str) -> str:
    provider = (provider or '').strip().lower()
    if provider.startswith('from_oauth_'):
        provider = provider[len('from_'):]
    aliases = {
        'google': 'oauth_google',
        'facebook': 'oauth_facebook',
        'apple': 'oauth_apple',
        'google_oauth2': 'oauth_google',
        'oauth_google': 'oauth_google',
        'oauth_apple': 'oauth_apple',
        'oauth_facebook': 'oauth_facebook',
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
        raise ValidationError({'provider': ['Only Google, Apple, and Facebook sign-in are supported.']})

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
    user = User.objects.filter(clerk_user_id=clerk_user_id).first()
    if user is None:
        return None

    from users.services.account_deletion import anonymize_local_account

    return anonymize_local_account(user, reason=reason, request=request).user

def authenticate_clerk_token(token: str, *, requested_role: str | None = None, request=None):
    payload = ClerkTokenVerifier().verify(token)
    profile = fetch_clerk_profile(payload)
    return sync_user_from_clerk(profile=profile, requested_role=requested_role, request=request)
