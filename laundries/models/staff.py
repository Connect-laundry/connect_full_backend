import uuid
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _


class LaundryStaff(models.Model):
    """Represents a staff member assigned to a laundry with a specific role."""

    class StaffRole(models.TextChoices):
        MANAGER = 'MANAGER', _('Manager')
        WASHER = 'WASHER', _('Washer')
        IRONER = 'IRONER', _('Ironer')
        DRIVER = 'DRIVER', _('Driver')
        RECEPTIONIST = 'RECEPTIONIST', _('Receptionist')

    class InviteStatus(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        DECLINED = 'DECLINED', _('Declined')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='staff_members'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='staff_assignments',
        null=True, blank=True,
        help_text="Linked user account (set when invite is accepted)"
    )
    email = models.EmailField(_('invite email'))
    phone = models.CharField(_('invite phone'), max_length=20, blank=True)
    name = models.CharField(_('staff name'), max_length=150)
    role = models.CharField(
        _('role'), max_length=20,
        choices=StaffRole.choices, default=StaffRole.WASHER
    )
    invite_status = models.CharField(
        _('invite status'), max_length=20,
        choices=InviteStatus.choices, default=InviteStatus.PENDING
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Laundry Staff')
        verbose_name_plural = _('Laundry Staff')
        unique_together = ('laundry', 'email')
        ordering = ['name']
        indexes = [
            models.Index(fields=['laundry', 'role']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) at {self.laundry.name}"
