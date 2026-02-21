# pyre-ignore[missing-module]
from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_user_referral_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='clerk_id',
        ),
    ]
