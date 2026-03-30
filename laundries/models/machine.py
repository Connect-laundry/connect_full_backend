import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _


class Machine(models.Model):
    """Represents a physical washing/drying machine in a laundry."""

    class MachineType(models.TextChoices):
        WASHER = "WASHER", _("Washing Machine")
        DRYER = "DRYER", _("Dryer")
        IRONER = "IRONER", _("Ironing Press")
        OTHER = "OTHER", _("Other")

    class MachineStatus(models.TextChoices):
        IDLE = "IDLE", _("Idle")
        BUSY = "BUSY", _("In Use")
        MAINTENANCE = "MAINTENANCE", _("Under Maintenance")
        OUT_OF_ORDER = "OUT_OF_ORDER", _("Out of Order")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey(
        "laundries.Laundry", on_delete=models.CASCADE, related_name="machines"
    )
    name = models.CharField(
        _("machine name"), max_length=100, help_text="e.g. 'Washer #1', 'Samsung Dryer'"
    )
    machine_type = models.CharField(
        _("type"),
        max_length=20,
        choices=MachineType.choices,
        default=MachineType.WASHER,
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=MachineStatus.choices,
        default=MachineStatus.IDLE,
    )
    notes = models.TextField(
        _("notes"), blank=True, help_text="Maintenance notes or special instructions"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Machine")
        verbose_name_plural = _("Machines")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["laundry", "status"]),
        ]

    def __str__(self):
        return f"{
            self.name} ({
            self.get_machine_type_display()}) - {
            self.get_status_display()}"
