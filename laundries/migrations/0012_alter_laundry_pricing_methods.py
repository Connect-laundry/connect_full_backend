from django.db import migrations, models
import django.contrib.postgres.fields

class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0011_laundry_min_weight_laundry_price_per_kg_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='laundry',
            name='pricing_methods',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(choices=[('PER_ITEM', 'Per Item'), ('PER_KG', 'Per Kg')], max_length=20), default=list, size=None),
        ),
    ]
