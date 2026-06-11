"""Audit logging service.

Single entry point for writing AuditLog rows from admin actions, permission
denials, and security events. Failures here must never break the caller, so all
writes are best-effort and swallow exceptions (logged).
"""
import logging

from marketplace.models import AuditLog

logger = logging.getLogger(__name__)


def _client_ip(request):
    if request is None:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def record_audit(
    *,
    action,
    actor=None,
    request=None,
    target_type='',
    target_id='',
    target_repr='',
    metadata=None,
    ip_address=None,
):
    """Persist an audit entry. Best-effort: never raises to the caller."""
    try:
        if actor is None and request is not None:
            actor = getattr(request, 'user', None)
        if actor is not None and not getattr(actor, 'is_authenticated', False):
            actor = None

        return AuditLog.objects.create(
            actor=actor,
            actor_email=getattr(actor, 'email', '') or '',
            action=action,
            target_type=target_type or '',
            target_id=str(target_id or ''),
            target_repr=(target_repr or '')[:255],
            metadata=metadata or {},
            ip_address=ip_address or _client_ip(request),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to write audit log", extra={"action": action, "error": str(exc)})
        return None
