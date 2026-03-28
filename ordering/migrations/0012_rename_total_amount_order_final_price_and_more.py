from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0011_order_actual_weight_order_estimated_weight_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='actual_weight',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='estimated_weight',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
