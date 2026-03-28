from django.db import migrations, models
import django.contrib.postgres.fields

class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0010_laundrystaff_machine'),
    ]

    operations = [
        migrations.AddField(
            model_name='laundry',
            name='price_per_kg',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='laundry',
            name='pricing_methods',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(choices=[('PER_ITEM', 'Per Item'), ('PER_KG', 'Per Kg')], max_length=20), default=list, size=None),
        ),
    ]
