"""Normalise stored phone numbers to E.164 (+233XXXXXXXXX).

Registration stored numbers exactly as typed, and the normaliser accepted
'+0245738120' as international, so production holds the same kind of number
in several shapes. Only unambiguous numbers are rewritten: a value that does
not normalise, or whose normalised form belongs to another account, is left
as it is (and logged) rather than guessed at.
"""
import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def normalise(apps, schema_editor):
    from users.utils.phone import PhoneValidationError, normalize_phone

    User = apps.get_model('users', 'User')
    taken = set(User.objects.exclude(phone__isnull=True).values_list('phone', flat=True))
    changed = skipped = 0
    for user in User.objects.exclude(phone__isnull=True).exclude(phone='').only('id', 'phone').iterator():
        if user.phone.startswith('deleted-'):
            continue  # anonymised tombstones
        try:
            e164 = normalize_phone(user.phone)
        except PhoneValidationError:
            skipped += 1
            continue
        if e164 == user.phone:
            continue
        if e164 in taken:
            skipped += 1
            logger.warning('Phone normalisation skipped: duplicate', extra={'user_id': str(user.id)})
            continue
        taken.discard(user.phone)
        taken.add(e164)
        User.objects.filter(pk=user.pk).update(phone=e164)
        changed += 1
    if changed or skipped:
        logger.info('Phone numbers normalised', extra={'changed': changed, 'skipped': skipped})


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0014_throttle_cache_table'),
    ]

    operations = [
        migrations.RunPython(normalise, migrations.RunPython.noop),
    ]
