from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from django.conf import settings # type: ignore
from django.db import transaction # type: ignore
from django.utils import timezone # type: ignore
from rest_framework.exceptions import AuthenticationFailed, ValidationError # type: ignore

from marketplace.models import AuditLog
from marketplace.services.audit import record_audit
from users.models import ClerkWebhookEvent, User
from users.services.clerk_service import (
    deactivate_user_from_clerk,
    profile_from_clerk_user_data,
    sync_user_from_clerk,
)

logger = logging.getLogger(__name__)

USER_EVENTS = {'user.created', 'user.updated'}
SESSION_EVENTS = {'session.created', 'session.ended'}
SUPPORTED_EVENTS = USER_EVENTS | {'user.deleted'} | SESSION_EVENTS


def _secret_bytes(secret: str) -> bytes:
    value = (secret or '').strip()
    if value.startswith('whsec_'):
        value = value.removeprefix('whsec_')
        return base64.b64decode(value)
    return value.encode('utf-8')


def _signature_values(signature_header: str) -> list[str]:
    values: list[str] = []
    for part in signature_header.split(' '):
        if not part:
            continue
        if ',' in part:
            version, signature = part.split(',', 1)
        elif '=' in part:
            version, signature = part.split('=', 1)
        else:
            continue
        if version == 'v1' and signature:
            values.append(signature.strip())
    return values


def verify_clerk_webhook_signature(*, body: bytes, svix_id: str, svix_timestamp: str, svix_signature: str) -> None:
    secret = getattr(settings, 'CLERK_WEBHOOK_SECRET', '')
    if not secret:
        raise AuthenticationFailed('Clerk webhook signing is not configured.')
    if not svix_id or not svix_timestamp or not svix_signature:
        raise AuthenticationFailed('Missing Clerk webhook signature headers.')

    try:
        timestamp = int(svix_timestamp)
    except (TypeError, ValueError) as exc:
        raise AuthenticationFailed('Invalid Clerk webhook timestamp.') from exc

    tolerance = int(getattr(settings, 'CLERK_WEBHOOK_TOLERANCE_SECONDS', 300))
    if abs(int(time.time()) - timestamp) > tolerance:
        raise AuthenticationFailed('Stale Clerk webhook timestamp.')

    signed_content = f'{svix_id}.{svix_timestamp}.'.encode('utf-8') + body
    expected = base64.b64encode(
        hmac.new(_secret_bytes(secret), signed_content, hashlib.sha256).digest()
    ).decode('utf-8')

    if not any(hmac.compare_digest(expected, candidate) for candidate in _signature_values(svix_signature)):
        raise AuthenticationFailed('Invalid Clerk webhook signature.')


def _clerk_user_id(payload: dict[str, Any]) -> str:
    data = payload.get('data') or {}
    return data.get('id') or data.get('user_id') or ''


def _record_webhook_audit(event: ClerkWebhookEvent, *, created_user: bool | None = None):
    record_audit(
        action=AuditLog.Action.SECURITY_EVENT,
        target_type='ClerkWebhookEvent',
        target_id=str(event.id),
        target_repr=event.event_type,
        metadata={
            'event': 'clerk_webhook_processed',
            'event_type': event.event_type,
            'svix_id': event.svix_id,
            'clerk_user_id': event.clerk_user_id,
            'created_user': created_user,
        },
    )


def _handle_user_event(payload: dict[str, Any], *, source: str) -> tuple[User, bool]:
    profile = profile_from_clerk_user_data(payload['data'], payload)
    return sync_user_from_clerk(
        profile=profile,
        source=source,
        require_verified_email=payload.get('type') != 'user.updated',
    )


def _handle_session_created(payload: dict[str, Any]):
    data = payload.get('data') or {}
    clerk_user_id = data.get('user_id') or ''
    if not clerk_user_id:
        return None
    user = User.objects.filter(clerk_user_id=clerk_user_id).first()
    if user is None:
        return None
    now = timezone.now()
    user.last_clerk_sign_in_at = now
    user.last_clerk_sync = now
    user.save(update_fields=['last_clerk_sign_in_at', 'last_clerk_sync', 'updated_at'])
    return user


def process_clerk_webhook(*, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    normalized_headers = {str(key).lower(): value for key, value in headers.items()}
    svix_id = normalized_headers.get('svix-id') or normalized_headers.get('http_svix_id') or ''
    svix_timestamp = normalized_headers.get('svix-timestamp') or normalized_headers.get('http_svix_timestamp') or ''
    svix_signature = normalized_headers.get('svix-signature') or normalized_headers.get('http_svix_signature') or ''

    verify_clerk_webhook_signature(
        body=body,
        svix_id=svix_id,
        svix_timestamp=svix_timestamp,
        svix_signature=svix_signature,
    )

    try:
        payload = json.loads(body.decode('utf-8'))
    except json.JSONDecodeError as exc:
        raise ValidationError({'payload': ['Invalid JSON payload.']}) from exc

    event_type = payload.get('type') or ''
    if event_type not in SUPPORTED_EVENTS:
        return {'status': 'ignored', 'event_type': event_type}

    clerk_user_id = _clerk_user_id(payload)
    with transaction.atomic():
        event, created = ClerkWebhookEvent.objects.select_for_update().get_or_create(
            svix_id=svix_id,
            defaults={
                'event_type': event_type,
                'clerk_user_id': clerk_user_id,
                'status': ClerkWebhookEvent.Status.PROCESSING,
            },
        )
        if not created and event.status == ClerkWebhookEvent.Status.PROCESSED:
            return {'status': 'duplicate', 'event_type': event.event_type}

        event.event_type = event_type
        event.clerk_user_id = clerk_user_id
        event.status = ClerkWebhookEvent.Status.PROCESSING
        event.error = ''
        event.save(update_fields=['event_type', 'clerk_user_id', 'status', 'error'])

        created_user: bool | None = None
        try:
            if event_type in USER_EVENTS:
                _, created_user = _handle_user_event(payload, source=f'webhook:{event_type}')
            elif event_type == 'user.deleted':
                deactivate_user_from_clerk(clerk_user_id, reason='clerk_user_deleted')
            elif event_type == 'session.created':
                _handle_session_created(payload)

            event.status = ClerkWebhookEvent.Status.PROCESSED
            event.processed_at = timezone.now()
            event.save(update_fields=['status', 'processed_at'])
            _record_webhook_audit(event, created_user=created_user)
        except Exception as exc:
            logger.warning(
                'Clerk webhook processing failed',
                extra={'event_type': event_type, 'svix_id': svix_id, 'error_type': type(exc).__name__},
            )
            event.status = ClerkWebhookEvent.Status.FAILED
            event.error = type(exc).__name__
            event.processed_at = timezone.now()
            event.save(update_fields=['status', 'error', 'processed_at'])
            raise

    return {'status': 'processed', 'event_type': event_type}
