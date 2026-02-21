# pyre-ignore[missing-module]
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0004_laundry_approved_at_laundry_delivery_fee_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='laundry',
            name='min_order',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10, verbose_name='minimum order value'),
        ),
    ]
