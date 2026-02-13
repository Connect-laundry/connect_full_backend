import uuid
# pyre-ignore[missing-module]
from django.db import models
# pyre-ignore[missing-module]
from django.utils.translation import gettext_lazy as _

class OpeningHours(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 1, _('Monday')
        TUESDAY = 2, _('Tuesday')
        WEDNESDAY = 3, _('Wednesday')
        THURSDAY = 4, _('Thursday')
        FRIDAY = 5, _('Friday')
        SATURDAY = 6, _('Saturday')
        SUNDAY = 7, _('Sunday')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    laundry = models.ForeignKey('laundries.Laundry', on_delete=models.CASCADE, related_name='opening_hours')
    
    day = models.IntegerField(choices=Weekday.choices)
    opening_time = models.TimeField()
    closing_time = models.TimeField()
    is_closed = models.BooleanField(default=False)

    class Meta:
        unique_together = ('laundry', 'day')
        verbose_name = _('Opening Hours')
        verbose_name_plural = _('Opening Hours')
        ordering = ['day', 'opening_time']

    def __str__(self):
        return f"{self.laundry.name} - {self.get_day_display()}: {self.opening_time} to {self.closing_time}"
