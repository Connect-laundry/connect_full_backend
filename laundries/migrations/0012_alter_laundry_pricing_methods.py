import django.contrib.postgres.fields
from django.db import migrations, models


def migrate_json_to_array(apps, schema_editor):
    Laundry = apps.get_model('laundries', 'Laundry')
    for laundry in Laundry.objects.all():
        if isinstance(laundry.pricing_methods_old, list):
            laundry.pricing_methods = laundry.pricing_methods_old
            laundry.save()


class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0011_laundry_min_weight_laundry_price_per_kg_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='laundry',
            old_name='pricing_methods',
            new_name='pricing_methods_old',
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
        migrations.RunPython(migrate_json_to_array, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='laundry',
            name='pricing_methods_old',
        ),
    ]
