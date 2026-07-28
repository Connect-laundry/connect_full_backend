import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.core.validators import MinValueValidator, MaxValueValidator
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        'laundries.Laundry',
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='laundry_reviews'
    )
    rating = models.PositiveSmallIntegerField(
        _('rating'),
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(_('comment'), blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Review')
        verbose_name_plural = _('Reviews')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['laundry', 'rating']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['laundry', 'user'],
                name='unique_review_per_user_per_laundry',
            ),
        ]

    def __str__(self):
        user_email = self.user.email if getattr(self, 'user', None) else 'Unknown'
        laundry_name = self.laundry.name if getattr(self, 'laundry', None) else 'Unknown'
        return f"Review by {user_email} for {laundry_name}"
