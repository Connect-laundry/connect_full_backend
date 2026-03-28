from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0010_add_gps_coordinates'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='estimated_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='final_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
