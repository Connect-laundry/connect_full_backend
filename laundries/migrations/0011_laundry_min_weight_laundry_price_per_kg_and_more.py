from django.db import migrations, models
import django.contrib.postgres.fields

class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0010_laundrystaff_machine'),
    ]

    operations = [
        migrations.AddField(
            model_name='laundry',
            name='min_weight',
            field=models.DecimalField(decimal_places=2, default=1.0, max_digits=5, verbose_name='minimum weight'),
        ),
        migrations.AddField(
            model_name='laundry',
            name='price_per_kg',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10, verbose_name='price per kg'),
        ),
        migrations.AddField(
            model_name='laundry',
            name='pricing_methods',
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(
                    choices=[('PER_ITEM', 'Per Item'), ('PER_KG', 'Per Kg')], 
                    max_length=20
                ), 
                blank=True,
                default=list, 
                size=None
            ),
        ),
    ]
