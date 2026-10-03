"""Revoke Sign in with Apple when a customer deletes their account.

App Store Guideline 5.1.1(v): apps offering Sign in with Apple must revoke the
user's Apple tokens through Apple's REST API when the account is deleted, so
Simame disappears from the person's "Sign in with Apple" list. Clerk holds the
Apple tokens and does not revoke them when its user is deleted, so this runs
before the Clerk user is removed.

Best effort by design: a missing configuration or an Apple/Clerk outage is
logged and never blocks the deletion itself.

Configuration (the same Apple key Clerk's Apple connection uses):
  APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY (the .p8 contents),
  APPLE_CLIENT_ID (the Services ID, or bundle ID for native sign-in, that
  issued the tokens).
"""
import logging
import time

import jwt  # type: ignore
import requests  # type: ignore
from django.conf import settings  # type: ignore

logger = logging.getLogger(__name__)

APPLE_REVOKE_URL = 'https://appleid.apple.com/auth/revoke'
APPLE_AUDIENCE = 'https://appleid.apple.com'


def _apple_config():
    config = {
        'team_id': getattr(settings, 'APPLE_TEAM_ID', ''),
        'key_id': getattr(settings, 'APPLE_KEY_ID', ''),
        'private_key': getattr(settings, 'APPLE_PRIVATE_KEY', '').replace('\\n', '\n'),
        'client_id': getattr(settings, 'APPLE_CLIENT_ID', ''),
    }
    return config if all(config.values()) else None


def _client_secret(config) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            'iss': config['team_id'],
            'iat': now,
            'exp': now + 300,
            'aud': APPLE_AUDIENCE,
            'sub': config['client_id'],
        },
        config['private_key'],
        algorithm='ES256',
        headers={'kid': config['key_id']},
    )


def _apple_tokens_from_clerk(clerk_user_id: str) -> list[str]:
    response = requests.get(
        f"{getattr(settings, 'CLERK_API_BASE_URL', 'https://api.clerk.com').rstrip('/')}"
        f"/v1/users/{clerk_user_id}/oauth_access_tokens/oauth_apple",
        headers={'Authorization': f'Bearer {settings.CLERK_SECRET_KEY}'},
        timeout=getattr(settings, 'CLERK_API_TIMEOUT_SECONDS', 5),
    )
    if response.status_code in (404, 422):  # no Apple account linked
        return []
    response.raise_for_status()
    payload = response.json()
    rows = payload.get('data', payload) if isinstance(payload, dict) else payload
    return [row['token'] for row in rows or [] if isinstance(row, dict) and row.get('token')]


def revoke_apple_sign_in(clerk_user_id: str) -> bool:
    """Revoke every Apple token Clerk holds for this user. True if any was revoked."""
    if not clerk_user_id or not getattr(settings, 'CLERK_SECRET_KEY', ''):
        return False
    config = _apple_config()
    if config is None:
        logger.info(
            'Apple sign-in revocation skipped: set APPLE_TEAM_ID, APPLE_KEY_ID, '
            'APPLE_PRIVATE_KEY and APPLE_CLIENT_ID to enable it')
        return False
    try:
        tokens = _apple_tokens_from_clerk(clerk_user_id)
        if not tokens:
            return False
        client_secret = _client_secret(config)
        revoked = False
        for token in tokens:
            response = requests.post(
                APPLE_REVOKE_URL,
                data={
                    'client_id': config['client_id'],
                    'client_secret': client_secret,
                    'token': token,
                    'token_type_hint': 'access_token',
                },
                timeout=10,
            )
            if response.status_code == 200:
                revoked = True
            else:
                logger.warning('Apple token revocation returned HTTP %s', response.status_code)
        return revoked
    except (requests.RequestException, ValueError, jwt.PyJWTError) as exc:
        logger.warning('Apple token revocation failed: %s (%s)', exc, type(exc).__name__)
        return False
