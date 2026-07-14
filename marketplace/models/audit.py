import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _


class AuditLog(models.Model):
    """Immutable record of sensitive administrative/security actions.

    Written by marketplace.services.audit.record_audit() from admin actions,
    permission denials, and security events. Read-only via the admin audit-log
    API (ADMIN only).
    """

    class Action(models.TextChoices):
        ADMIN_SEARCH = 'ADMIN_SEARCH', _('Admin Search')
        USER_ROLE_CHANGED = 'USER_ROLE_CHANGED', _('User Role Changed')
        USER_EDITED = 'USER_EDITED', _('User Edited')
        LAUNDRY_APPROVED = 'LAUNDRY_APPROVED', _('Laundry Approved')
        LAUNDRY_REJECTED = 'LAUNDRY_REJECTED', _('Laundry Rejected')
        LAUNDRY_CHANGES_REQUESTED = 'LAUNDRY_CHANGES_REQUESTED', _('Laundry Changes Requested')
        LAUNDRY_SUSPENDED = 'LAUNDRY_SUSPENDED', _('Laundry Suspended')
        LAUNDRY_RESUBMITTED = 'LAUNDRY_RESUBMITTED', _('Laundry Resubmitted')
        ORDER_STATUS_CHANGED = 'ORDER_STATUS_CHANGED', _('Order Status Changed')
        PAYMENT_ACTION = 'PAYMENT_ACTION', _('Payment Action')
        COUPON_CREATED = 'COUPON_CREATED', _('Coupon Created')
        NOTIFICATION_DISMISSED = 'NOTIFICATION_DISMISSED', _('Notification Dismissed')
        LEGAL_DOCUMENT_CREATED = 'LEGAL_DOCUMENT_CREATED', _('Legal Document Created')
        LEGAL_DOCUMENT_UPDATED = 'LEGAL_DOCUMENT_UPDATED', _('Legal Document Updated')
        LEGAL_DOCUMENT_PUBLISHED = 'LEGAL_DOCUMENT_PUBLISHED', _('Legal Document Published')
        LEGAL_DOCUMENT_ARCHIVED = 'LEGAL_DOCUMENT_ARCHIVED', _('Legal Document Archived')
        LEGAL_DOCUMENT_ROLLED_BACK = 'LEGAL_DOCUMENT_ROLLED_BACK', _('Legal Document Rolled Back')
        LEGAL_ACCEPTANCE_RECORDED = 'LEGAL_ACCEPTANCE_RECORDED', _('Legal Acceptance Recorded')
        PERMISSION_DENIED = 'PERMISSION_DENIED', _('Permission Denied')
        SECURITY_EVENT = 'SECURITY_EVENT', _('Security Event')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_events',
    )
    actor_email = models.CharField(max_length=254, blank=True, default='')
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    # Generic target reference (no hard FK, so logs survive target deletion).
    target_type = models.CharField(max_length=60, blank=True, default='', db_index=True)
    target_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    target_repr = models.CharField(max_length=255, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Audit Log')
        verbose_name_plural = _('Audit Logs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['actor', 'created_at']),
            models.Index(fields=['target_type', 'target_id']),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_email or 'system'} @ {self.created_at:%Y-%m-%d %H:%M}"
