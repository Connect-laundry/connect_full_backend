"""End-to-end (API level) lifecycle of a brand-new Sign in with Apple customer.

Only the two external boundaries are faked: Clerk's token verification/profile
lookup and the Apple/Clerk calls made on deletion. Everything else (views,
serializers, user sync, sessions, refresh rotation, deletion) is the real code.
"""
import pytest
from rest_framework.test import APIClient

from users.models import User
from users.services.clerk_service import ClerkProfile

RELAY = 'k9x2m7q4tz@privaterelay.appleid.com'


def _apple_profile(clerk_user_id):
    # Hide My Email + "don't share name": Apple gives a relay address, no name.
    return ClerkProfile(clerk_user_id=clerk_user_id, email=RELAY, first_name='', last_name='',
                        image_url='', provider='oauth_token_apple')


@pytest.fixture
def clerk(monkeypatch):
    state = {'sub': 'user_apple_first'}
    monkeypatch.setattr('users.services.clerk_service.ClerkTokenVerifier.verify',
                        lambda _self, _token: {'sub': state['sub']})
    monkeypatch.setattr('users.services.clerk_service.fetch_clerk_profile',
                        lambda payload, prefer_claims=False: _apple_profile(payload['sub']))
    deletions = []
    monkeypatch.setattr('users.views.profile.delete_clerk_user', lambda uid: deletions.append(uid))
    monkeypatch.setattr('users.views.profile.revoke_apple_for_account_deletion',
                        lambda uid, code: 'revoked' if code else 'no_code')
    state['deletions'] = deletions
    return state


def _social_login(client):
    return client.post('/api/v1/auth/social-login/', {'clerk_token': 'clerk-session-jwt', 'role': 'CUSTOMER'},
                       format='json', HTTP_X_DEVICE_ID='device-lifecycle-1')


@pytest.mark.django_db
def test_new_apple_customer_full_lifecycle(clerk):
    client = APIClient()

    # 1. First sign-in creates a verified CUSTOMER with the relay email.
    first = _social_login(client)
    assert first.status_code == 200, first.content[:300]
    body = first.json()
    user = User.objects.get(clerk_user_id='user_apple_first')
    assert user.role == User.Role.CUSTOMER
    assert user.email == RELAY and user.is_verified and user.is_active
    assert body['user']['role'] == User.Role.CUSTOMER
    assert body['user']['fullName']  # never blank, even without a name from Apple
    assert body['accessToken'] and body['refreshToken']

    # 2. The access token works on an authenticated endpoint.
    me = APIClient()
    me.credentials(HTTP_AUTHORIZATION=f"Bearer {body['accessToken']}")
    profile = me.get('/api/v1/auth/me/')
    assert profile.status_code == 200, profile.content[:300]

    # 3. Returning sign-in reaches the same account, no duplicate.
    again = _social_login(client)
    assert again.status_code == 200
    assert again.json()['user']['id'] == str(user.id)
    assert User.objects.filter(email=RELAY).count() == 1

    # 4. Session renewal: the refresh token rotates and keeps the session.
    refreshed = client.post('/api/v1/auth/token/refresh/', {'refresh': again.json()['refreshToken']},
                            format='json', HTTP_X_DEVICE_ID='device-lifecycle-1')
    assert refreshed.status_code == 200, refreshed.content[:300]
    new_access = refreshed.json().get('access') or refreshed.json().get('accessToken') or \
        (refreshed.json().get('data') or {}).get('access')
    assert new_access

    # 5. Deletion with a fresh Apple code revokes and anonymizes.
    me.credentials(HTTP_AUTHORIZATION=f'Bearer {new_access}')
    deleted = me.delete('/api/v1/auth/account/', {'reason': 'other', 'apple_authorization_code': 'c-fresh'},
                        format='json')
    assert deleted.status_code == 200, deleted.content[:300]
    assert deleted.json()['data']['apple_revocation'] == 'revoked'
    assert clerk['deletions'] == ['user_apple_first']
    user.refresh_from_db()
    assert not user.is_active and user.clerk_user_id is None and user.email.startswith('deleted-')

    # 6. Old tokens no longer work.
    assert me.get('/api/v1/auth/me/').status_code == 401

    # 7. Signing in again with the same Apple ID (new Clerk user) is a NEW customer.
    clerk['sub'] = 'user_apple_second'
    reborn = _social_login(client)
    assert reborn.status_code == 200, reborn.content[:300]
    assert reborn.json()['user']['id'] != str(user.id)
    assert User.objects.get(clerk_user_id='user_apple_second').role == User.Role.CUSTOMER
