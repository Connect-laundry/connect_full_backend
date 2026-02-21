# pyre-ignore[missing-module]
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0002_failedtask_notification'),
    ]

    operations = [
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
