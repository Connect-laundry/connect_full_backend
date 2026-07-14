"""Laundry approval workflow — the single source of truth for status transitions.

Every path that approves / rejects / requests changes on / suspends a laundry
(Django admin buttons, DRF admin endpoints, owner resubmission) must go through
:class:`LaundryApprovalService`. Each transition is atomic; side effects
(notifications, emails, analytics) are queued via ``transaction.on_commit`` and
are best-effort — a notification failure never rolls back a decision.
"""
import logging

from django.db import transaction
from django.utils import timezone

from ..models.laundry import Laundry, OwnerAuditLog

logger = logging.getLogger(__name__)


class InvalidTransition(Exception):
    """Raised when a status transition is not allowed from the current state."""


# state -> set of states it may move to (admin-driven)
_ALLOWED = {
    Laundry.ApprovalStatus.PENDING: {
        Laundry.ApprovalStatus.APPROVED,
        Laundry.ApprovalStatus.REJECTED,
        Laundry.ApprovalStatus.CHANGES_REQUESTED,
    },
    Laundry.ApprovalStatus.APPROVED: {
        Laundry.ApprovalStatus.SUSPENDED,
        Laundry.ApprovalStatus.REJECTED,
    },
    Laundry.ApprovalStatus.REJECTED: {
        Laundry.ApprovalStatus.APPROVED,
        Laundry.ApprovalStatus.PENDING,
    },
    Laundry.ApprovalStatus.CHANGES_REQUESTED: {
        Laundry.ApprovalStatus.APPROVED,
        Laundry.ApprovalStatus.REJECTED,
        Laundry.ApprovalStatus.PENDING,
    },
    Laundry.ApprovalStatus.SUSPENDED: {
        Laundry.ApprovalStatus.APPROVED,
        Laundry.ApprovalStatus.REJECTED,
    },
}


class LaundryApprovalService:
    """Atomic laundry status transitions with audit, notifications & analytics."""

    # ------------------------------------------------------------------ public

    @classmethod
    def approve(cls, laundry, *, actor, request=None):
        return cls._transition(
            laundry,
            Laundry.ApprovalStatus.APPROVED,
            actor=actor,
            request=request,
            reason='',
        )

    @classmethod
    def reject(cls, laundry, *, actor, reason='', request=None):
        return cls._transition(
            laundry,
            Laundry.ApprovalStatus.REJECTED,
            actor=actor,
            request=request,
            reason=reason,
        )

    @classmethod
    def request_changes(cls, laundry, *, actor, reason='', request=None):
        return cls._transition(
            laundry,
            Laundry.ApprovalStatus.CHANGES_REQUESTED,
            actor=actor,
            request=request,
            reason=reason,
        )

    @classmethod
    def suspend(cls, laundry, *, actor, reason='', request=None):
        return cls._transition(
            laundry,
            Laundry.ApprovalStatus.SUSPENDED,
            actor=actor,
            request=request,
            reason=reason,
        )

    @classmethod
    def resubmit(cls, laundry, *, actor):
        """Owner edited a laundry that had changes requested (or was rejected):
        it automatically returns to the review queue."""
        old_status = laundry.status
        if old_status not in (
            Laundry.ApprovalStatus.CHANGES_REQUESTED,
            Laundry.ApprovalStatus.REJECTED,
        ):
            return laundry

        now = timezone.now()
        with transaction.atomic():
            laundry.status = Laundry.ApprovalStatus.PENDING
            laundry.is_active = False
            laundry.submitted_at = now
            laundry.status_reason = ''
            laundry.save(update_fields=[
                'status', 'is_active', 'submitted_at', 'status_reason', 'updated_at',
            ])
            OwnerAuditLog.objects.create(
                laundry=laundry,
                actor=actor,
                action='LAUNDRY_RESUBMITTED',
                details={'previous_status': old_status},
            )
            transaction.on_commit(
                lambda: cls._after_resubmit(laundry, old_status)
            )
        return laundry

    # -------------------------------------------------------------- internals

    @classmethod
    def _transition(cls, laundry, new_status, *, actor, request, reason):
        old_status = laundry.status
        if new_status not in _ALLOWED.get(old_status, set()):
            raise InvalidTransition(
                f"Cannot move laundry from {old_status} to {new_status}."
            )

        now = timezone.now()
        with transaction.atomic():
            locked = Laundry.objects.select_for_update().get(pk=laundry.pk)
            if locked.status != old_status:
                # Someone else decided first; treat repeat of same decision as no-op.
                if locked.status == new_status:
                    return locked
                raise InvalidTransition(
                    f"Laundry status changed to {locked.status} while you were reviewing."
                )

            locked.status = new_status
            locked.status_reason = reason or ''
            locked.reviewed_by = actor
            update_fields = [
                'status', 'status_reason', 'reviewed_by', 'is_active', 'updated_at',
            ]

            if new_status == Laundry.ApprovalStatus.APPROVED:
                locked.is_active = True
                locked.approved_at = now
                update_fields.append('approved_at')
            elif new_status == Laundry.ApprovalStatus.REJECTED:
                locked.is_active = False
                locked.rejected_at = now
                update_fields.append('rejected_at')
            elif new_status == Laundry.ApprovalStatus.CHANGES_REQUESTED:
                locked.is_active = False
                locked.changes_requested_at = now
                update_fields.append('changes_requested_at')
            elif new_status == Laundry.ApprovalStatus.SUSPENDED:
                locked.is_active = False

            locked.save(update_fields=update_fields)

            OwnerAuditLog.objects.create(
                laundry=locked,
                actor=actor,
                action=f'LAUNDRY_{new_status}',
                details={
                    'previous_status': old_status,
                    'new_status': new_status,
                    'reason': reason or '',
                },
            )

            cls._record_platform_audit(
                locked, old_status, new_status, actor=actor,
                request=request, reason=reason,
            )

            transaction.on_commit(
                lambda: cls._after_decision(locked, old_status, new_status, reason)
            )

        logger.info(
            "Laundry status transition",
            extra={
                "laundry_id": str(locked.id),
                "from": old_status,
                "to": new_status,
                "actor": getattr(actor, 'email', None),
            },
        )
        return locked

    @staticmethod
    def _record_platform_audit(laundry, old_status, new_status, *, actor, request, reason):
        """Platform-wide AuditLog row (best-effort by design of record_audit)."""
        from marketplace.models import AuditLog
        from marketplace.services.audit import record_audit

        action_map = {
            Laundry.ApprovalStatus.APPROVED: AuditLog.Action.LAUNDRY_APPROVED,
            Laundry.ApprovalStatus.REJECTED: AuditLog.Action.LAUNDRY_REJECTED,
            Laundry.ApprovalStatus.CHANGES_REQUESTED: AuditLog.Action.LAUNDRY_CHANGES_REQUESTED,
            Laundry.ApprovalStatus.SUSPENDED: AuditLog.Action.LAUNDRY_SUSPENDED,
        }
        record_audit(
            action=action_map[new_status],
            actor=actor,
            request=request,
            target_type='Laundry',
            target_id=str(laundry.id),
            target_repr=laundry.name,
            metadata={
                'old_status': old_status,
                'new_status': new_status,
                'reason': reason or '',
            },
        )

    # --------------------------------------------------- post-commit side effects

    @classmethod
    def _after_decision(cls, laundry, old_status, new_status, reason):
        """Runs after the transaction commits. Every step is independently
        best-effort: a failure is logged but never raises."""
        cls._safe(lambda: cls._track_event(laundry, old_status, new_status))
        cls._safe(lambda: cls._notify_owner(laundry, new_status, reason))
        cls._safe(lambda: cls._email_owner(laundry, new_status, reason))
        cls._safe(lambda: cls._mark_admin_notification_handled(laundry))

    @classmethod
    def _after_resubmit(cls, laundry, old_status):
        def _notify_admins():
            from marketplace.models import Notification
            from marketplace.services.notification_service import NotificationService
            NotificationService.notify_admins(
                title="Laundry resubmitted for approval",
                body=f"'{laundry.name}' was updated by its owner and is pending review again.",
                category='LAUNDRY_PENDING',
                priority=Notification.Priority.HIGH,
                action_url=f'/admin/laundries/laundry/{laundry.id}/change/',
                dedup_key=f'laundry_pending:{laundry.id}:{laundry.submitted_at:%Y%m%d%H%M%S}',
            )

        def _email_admins():
            from laundries.tasks import send_admin_new_laundry_email
            from utils.tasks import safe_task_delay
            safe_task_delay(send_admin_new_laundry_email, str(laundry.id), resubmission=True)

        cls._safe(_notify_admins)
        cls._safe(_email_admins)
        cls._safe(lambda: cls._track_event(laundry, old_status, Laundry.ApprovalStatus.PENDING))

    @staticmethod
    def _track_event(laundry, old_status, new_status):
        from analytics.models import AnalyticsEvent

        event_names = {
            Laundry.ApprovalStatus.PENDING: 'laundry_submitted',
            Laundry.ApprovalStatus.APPROVED: 'laundry_approved',
            Laundry.ApprovalStatus.REJECTED: 'laundry_rejected',
            Laundry.ApprovalStatus.CHANGES_REQUESTED: 'laundry_changes_requested',
            Laundry.ApprovalStatus.SUSPENDED: 'laundry_suspended',
        }
        submitted = laundry.submitted_at or laundry.created_at
        review_hours = None
        if submitted and new_status in (
            Laundry.ApprovalStatus.APPROVED,
            Laundry.ApprovalStatus.REJECTED,
            Laundry.ApprovalStatus.CHANGES_REQUESTED,
        ):
            review_hours = round(
                (timezone.now() - submitted).total_seconds() / 3600.0, 2
            )
        AnalyticsEvent.objects.create(
            event_name=event_names[new_status],
            platform=AnalyticsEvent.Platform.SERVER,
            event_data={
                'laundry_id': str(laundry.id),
                'laundry_name': laundry.name,
                'old_status': old_status,
                'new_status': new_status,
                'review_hours': review_hours,
            },
        )

    @staticmethod
    def _notify_owner(laundry, new_status, reason):
        from marketplace.models import Notification
        from marketplace.services.notification_service import NotificationService

        owner = laundry.owner
        if owner is None:
            return
        messages = {
            Laundry.ApprovalStatus.APPROVED: (
                "Laundry approved 🎉",
                f"Your laundry '{laundry.name}' has been approved and is now live on Connect.",
            ),
            Laundry.ApprovalStatus.REJECTED: (
                "Laundry not approved",
                f"Your laundry '{laundry.name}' was not approved."
                + (f" Reason: {reason}" if reason else ""),
            ),
            Laundry.ApprovalStatus.CHANGES_REQUESTED: (
                "Changes requested on your laundry",
                f"Please update '{laundry.name}' and resubmit."
                + (f" What to fix: {reason}" if reason else ""),
            ),
            Laundry.ApprovalStatus.SUSPENDED: (
                "Laundry suspended",
                f"Your laundry '{laundry.name}' has been suspended."
                + (f" Reason: {reason}" if reason else ""),
            ),
        }
        title, body = messages[new_status]
        NotificationService.notify_user(
            owner,
            title=title,
            body=body,
            category=f'LAUNDRY_{new_status}',
            priority=Notification.Priority.HIGH,
            dedup_key=f'laundry_{new_status.lower()}:{laundry.id}:{timezone.now():%Y%m%d%H%M}',
        )

    @staticmethod
    def _email_owner(laundry, new_status, reason):
        from laundries.tasks import send_owner_status_email
        from utils.tasks import safe_task_delay
        safe_task_delay(
            send_owner_status_email, str(laundry.id), new_status, reason or '',
        )

    @staticmethod
    def _mark_admin_notification_handled(laundry):
        """Collapse the 'awaiting approval' bell entry once a decision is made."""
        from marketplace.models import Notification
        Notification.objects.filter(
            audience=Notification.Audience.ADMIN,
            category='LAUNDRY_PENDING',
            is_read=False,
            dedup_key__startswith=f'laundry_pending:{laundry.id}',
        ).update(is_read=True, read_at=timezone.now())

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception as exc:  # pragma: no cover - side effects must not raise
            logger.error(
                "Laundry approval side effect failed", extra={"error": str(exc)}
            )
