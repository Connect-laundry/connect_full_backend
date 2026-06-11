# pyre-ignore[missing-module]
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0002_failedtask_notification'),
    ]

    operations = [
        # Drop the index on `recipient` BEFORE renaming the field. On SQLite the
        # RenameField remakes the whole table and rebuilds every index from
        # state; if this index still references the about-to-be-renamed
        # `recipient` column the remake raises FieldDoesNotExist. PostgreSQL does
        # not remake the table, which is why production applied this cleanly.
        # (This index is removed in 0005 on existing databases; doing it here is
        # schema-equivalent — 0005 re-adds the equivalent index on `user`.)
        migrations.RemoveIndex(
            model_name='notification',
            name='marketplace_recipie_40bafb_idx',
        ),
        migrations.RenameField(
            model_name='notification',
            old_name='recipient',
            new_name='user',
        ),
        migrations.RenameField(
            model_name='notification',
            old_name='message',
            new_name='body',
        ),
    ]
