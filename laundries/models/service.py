import uuid

# pyre-ignore[missing-module]
from django.db import models

# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _


class LaundryService(models.Model):
    """Bridge table mapping global items to specific laundries with custom pricing."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        "laundries.Laundry", on_delete=models.CASCADE, related_name="laundry_services"
    )
    item = models.ForeignKey(
        "ordering.LaunderableItem",
        on_delete=models.CASCADE,
        related_name="laundry_services",
    )
    service_type = models.ForeignKey(
        "laundries.Category",
        on_delete=models.CASCADE,
        related_name="laundry_services",
        limit_choices_to={"type": "SERVICE_TYPE"},
    )
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2)
    # E.g., '24 hours', '2 days'
    estimated_duration = models.CharField(
        _("estimated duration"), max_length=50, blank=True
    )
    is_available = models.BooleanField(_("is available"), default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Laundry Service")
        verbose_name_plural = _("Laundry Services")
        # A laundry can only define a specific service for a specific item once
        unique_together = ("laundry", "item", "service_type")
        ordering = ["laundry", "item__name"]

    def __str__(self):
        return f"{
            self.item.name} ({
            self.service_type.name}) - {
            self.price} at {
                self.laundry.name}"
