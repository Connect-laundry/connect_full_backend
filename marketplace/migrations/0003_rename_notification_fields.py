# pyre-ignore[missing-module]
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0002_failedtask_notification"),
    ]

    operations = [
        # Remove the composite index referencing 'recipient' BEFORE renaming
        # the field. SQLite's _remake_table recreates all indexes during
        # RenameField, which fails if the index references the old field name.
        migrations.RemoveIndex(
            model_name="notification",
            name="marketplace_recipie_40bafb_idx",
        ),
        migrations.RenameField(
            model_name="notification",
            old_name="recipient",
            new_name="user",
        ),
        migrations.RenameField(
            model_name="notification",
            old_name="message",
            new_name="body",
        ),
    ]
