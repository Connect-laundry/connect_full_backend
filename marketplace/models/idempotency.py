import uuid

# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _


class IdempotencyRecord(models.Model):
    """A claimed ``X-Idempotency-Key``, with the response once it is known.

    Deliberately database-backed rather than cache-backed. The cache falls back
    to per-process ``LocMemCache`` whenever Redis is unavailable, which means
    two gunicorn workers keep separate idempotency state — and two identical
    payment requests landing on different workers would both go through. The
    unique constraint below is shared by every worker and every instance.

    Rows are written on POST requests that carry the header (payments and
    bookings), so volume is low. Purge old ones with
    ``manage.py purge_idempotency_records``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # "<user or anon ip>:<client key>" — scoped so one client's key can never
    # collide with another's.
    key = models.CharField(max_length=255, unique=True, db_index=True)
    # Hash of method + path + body, so reusing a key for a *different* request
    # is rejected instead of silently replaying the wrong response.
    fingerprint = models.CharField(max_length=64)

    # Null while the original request is still in flight.
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    content_type = models.CharField(max_length=100, blank=True, default='application/json')
    content = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Idempotency Record')
        verbose_name_plural = _('Idempotency Records')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.key} ({self.status_code or 'in flight'})"

    @property
    def is_complete(self):
        return self.status_code is not None
