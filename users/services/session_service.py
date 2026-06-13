from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Any
import uuid

import jwt # type: ignore
from django.db import transaction # type: ignore
from django.utils import timezone # type: ignore
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied # type: ignore
from rest_framework_simplejwt.settings import api_settings # type: ignore
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken # type: ignore
from rest_framework_simplejwt.tokens import RefreshToken, TokenError # type: ignore

from users.models import DeviceSession, SessionRefreshToken, User

SESSION_ID_CLAIM = 'sid'
SESSION_FAMILY_CLAIM = 'sfi'
DEVICE_ID_CLAIM = 'device_id'


def _exp_to_datetime(exp_value: int | float | None):
    if not exp_value:
        return None
    return datetime.fromtimestamp(exp_value, tz=dt_timezone.utc)


def _decode_refresh_token(token: str, *, verify_exp: bool):
    options = {
        'verify_signature': True,
        'verify_exp': verify_exp,
        'verify_aud': bool(api_settings.AUDIENCE),
        'verify_iss': bool(api_settings.ISSUER),
    }
    kwargs: dict[str, Any] = {
        'key': api_settings.SIGNING_KEY,
        'algorithms': [api_settings.ALGORITHM],
        'options': options,
    }
    if api_settings.AUDIENCE:
        kwargs['audience'] = api_settings.AUDIENCE
    if api_settings.ISSUER:
        kwargs['issuer'] = api_settings.ISSUER
    return jwt.decode(token, **kwargs)


def get_request_session_id(request):
    auth = getattr(request, 'auth', None)
    if not auth:
        return None
    return auth.get(SESSION_ID_CLAIM)


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def get_device_context(request):
    return {
        'device_id': request.META.get('HTTP_X_DEVICE_ID', '').strip() or f"web-{uuid.uuid4()}",
        'platform': request.META.get('HTTP_X_CLIENT_PLATFORM', '').strip()[:32],
        'app_version': request.META.get('HTTP_X_CLIENT_VERSION', '').strip()[:32],
        'user_agent': (request.META.get('HTTP_USER_AGENT', '') or '')[:512],
        'ip_address': get_client_ip(request),
    }


def _blacklist_jti(jti: str):
    if not jti:
        return
    outstanding = OutstandingToken.objects.filter(jti=jti).first()
    if outstanding:
        BlacklistedToken.objects.get_or_create(token=outstanding)


def _touch_session(session: DeviceSession, *, jti: str | None = None, exp=None, request=None):
    changed_fields = ['last_used_at']
    session.last_used_at = timezone.now()
    if jti is not None:
        session.current_refresh_jti = jti
        changed_fields.append('current_refresh_jti')
    if exp is not None:
        session.current_refresh_expires_at = exp
        changed_fields.append('current_refresh_expires_at')
    if request is not None:
        device_context = get_device_context(request)
        session.ip_address = device_context['ip_address']
        session.user_agent = device_context['user_agent']
        session.platform = device_context['platform']
        session.app_version = device_context['app_version']
        changed_fields.extend(['ip_address', 'user_agent', 'platform', 'app_version'])
    session.save(update_fields=changed_fields)


def _create_refresh_record(session: DeviceSession, *, jti: str, exp):
    return SessionRefreshToken.objects.create(
        session=session,
        jti=jti,
        expires_at=exp,
    )


def _attach_session_claims(refresh: RefreshToken, *, session: DeviceSession, user: User):
    refresh['email'] = user.email
    refresh['role'] = user.role
    refresh[SESSION_ID_CLAIM] = str(session.id)
    refresh[SESSION_FAMILY_CLAIM] = str(session.session_family_id)
    refresh[DEVICE_ID_CLAIM] = session.device_id


def issue_tokens_for_user(user: User, request):
    device_context = get_device_context(request)
    # All session-side writes (device session, refresh-token record, session
    # touch) form one unit: either the caller gets a fully-consistent session or
    # nothing is persisted. Callers may nest this inside a wider transaction
    # (e.g. registration); transaction.atomic() handles nesting via savepoints.
    with transaction.atomic():
        session = DeviceSession.objects.create(
            user=user,
            device_id=device_context['device_id'],
            platform=device_context['platform'],
            app_version=device_context['app_version'],
            user_agent=device_context['user_agent'],
            ip_address=device_context['ip_address'],
        )

        refresh = RefreshToken.for_user(user)
        _attach_session_claims(refresh, session=session, user=user)

        refresh_jti = str(refresh[api_settings.JTI_CLAIM])
        refresh_exp = _exp_to_datetime(refresh.get('exp'))
        _create_refresh_record(session, jti=refresh_jti, exp=refresh_exp)
        _touch_session(session, jti=refresh_jti, exp=refresh_exp, request=request)

    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'session': session,
    }


def revoke_session(session: DeviceSession, *, reason: str):
    now = timezone.now()
    if session.revoked_at is None:
        session.revoked_at = now
        session.revoked_reason = reason
        session.save(update_fields=['revoked_at', 'revoked_reason'])

    refresh_tokens = session.refresh_tokens.filter(revoked_at__isnull=True) # type: ignore
    for refresh_token in refresh_tokens:
        refresh_token.revoked_at = now
        refresh_token.revoked_reason = reason
        refresh_token.save(update_fields=['revoked_at', 'revoked_reason'])
        _blacklist_jti(refresh_token.jti)


def revoke_all_sessions_for_user(user: User, *, reason: str):
    sessions = DeviceSession.objects.filter(user=user, revoked_at__isnull=True)
    for session in sessions:
        revoke_session(session, reason=reason)


def _create_migrated_session(user: User, request):
    device_context = get_device_context(request)
    return DeviceSession.objects.create(
        user=user,
        device_id=device_context['device_id'],
        platform=device_context['platform'],
        app_version=device_context['app_version'],
        user_agent=device_context['user_agent'],
        ip_address=device_context['ip_address'],
    )


def _resolve_session_for_payload(payload, request):
    session_id = payload.get(SESSION_ID_CLAIM)
    if session_id:
        session = DeviceSession.objects.filter(id=session_id).select_related('user').first()
        if session:
            return session

    old_jti = payload.get(api_settings.JTI_CLAIM)
    if old_jti:
        token_record = SessionRefreshToken.objects.filter(jti=old_jti).select_related('session', 'session__user').first()
        if token_record:
            return token_record.session

    user_id = payload.get(api_settings.USER_ID_CLAIM)
    if not user_id:
        raise AuthenticationFailed('Invalid refresh token.')
    user = User.objects.filter(id=user_id).first()
    if not user:
        raise AuthenticationFailed('Invalid refresh token.')
    return _create_migrated_session(user, request)


def rotate_refresh_token(submitted_refresh: str, request):
    try:
        payload = _decode_refresh_token(submitted_refresh, verify_exp=False)
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed('Invalid refresh token.') from exc

    old_jti = str(payload.get(api_settings.JTI_CLAIM, '') or '')
    if not old_jti:
        raise AuthenticationFailed('Invalid refresh token.')

    token_record = (
        SessionRefreshToken.objects.select_related('session', 'session__user')
        .filter(jti=old_jti)
        .first()
    )
    if token_record and not token_record.is_active:
        token_record.reuse_detected_at = timezone.now()
        token_record.save(update_fields=['reuse_detected_at'])
        revoke_session(token_record.session, reason='refresh_token_reuse')
        raise AuthenticationFailed('Session security event detected. Please sign in again.')

    try:
        refresh = RefreshToken(submitted_refresh)
    except TokenError as exc:
        if token_record:
            token_record.reuse_detected_at = timezone.now()
            token_record.save(update_fields=['reuse_detected_at'])
            revoke_session(token_record.session, reason='refresh_token_reuse')
        raise AuthenticationFailed('Refresh token is invalid or expired.') from exc

    with transaction.atomic():
        session = token_record.session if token_record else _resolve_session_for_payload(payload, request)
        if session.revoked_at is not None:
            raise AuthenticationFailed('Session has been revoked. Please sign in again.')

        user = session.user
        if not user.is_active:
            revoke_session(session, reason='user_inactive')
            raise AuthenticationFailed('User account is disabled.')

        if api_settings.BLACKLIST_AFTER_ROTATION:
            try:
                refresh.blacklist()
            except AttributeError:
                # Blacklist support is optional (e.g., blacklist app not installed); continue rotation.
                pass

        refresh.set_jti()
        refresh.set_exp()
        refresh.set_iat()
        _attach_session_claims(refresh, session=session, user=user)

        new_jti = str(refresh[api_settings.JTI_CLAIM])
        new_exp = _exp_to_datetime(refresh.get('exp'))
        now = timezone.now()

        if token_record:
            token_record.rotated_at = now
            token_record.replaced_by_jti = new_jti
            token_record.revoked_at = now
            token_record.revoked_reason = 'rotated'
            token_record.save(
                update_fields=['rotated_at', 'replaced_by_jti', 'revoked_at', 'revoked_reason']
            )

        _create_refresh_record(session, jti=new_jti, exp=new_exp)
        _touch_session(session, jti=new_jti, exp=new_exp, request=request)

        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'session': session,
        }


def revoke_current_session(user: User, *, submitted_refresh: str | None, request, reason: str):
    session = None
    token_record = None

    if submitted_refresh:
        try:
            payload = _decode_refresh_token(submitted_refresh, verify_exp=False)
        except jwt.PyJWTError as exc:
            raise AuthenticationFailed('Invalid refresh token.') from exc
        session_id = payload.get(SESSION_ID_CLAIM)
        refresh_jti = payload.get(api_settings.JTI_CLAIM)
        if session_id:
            session = DeviceSession.objects.filter(id=session_id, user=user).first()
        if session is None and refresh_jti:
            token_record = (
                SessionRefreshToken.objects.select_related('session')
                .filter(jti=refresh_jti, session__user=user)
                .first()
            )
            session = token_record.session if token_record else None
        try:
            RefreshToken(submitted_refresh).blacklist()
        except TokenError:
            # Token may be invalid/expired/already handled; session revocation still proceeds.
            pass
        except AttributeError:
            # Blacklist support may be unavailable; session revocation still proceeds.
            pass
    else:
        session_id = get_request_session_id(request)
        if session_id:
            session = DeviceSession.objects.filter(id=session_id, user=user).first()

    if session is None:
        raise PermissionDenied('Session not found.')

    revoke_session(session, reason=reason)


def mask_ip_address(ip_address: str | None):
    if not ip_address:
        return ''
    if ':' in ip_address:
        parts = ip_address.split(':')
        return ':'.join(parts[:3]) + ':****'
    parts = ip_address.split('.')
    if len(parts) == 4:
        return '.'.join(parts[:3]) + '.x'
    return ip_address
