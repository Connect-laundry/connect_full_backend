from django.db import migrations, models

def migrate_prices_backward_compatible(apps, schema_editor):
    Order = apps.get_model('ordering', 'Order')
    # Copy final_price (the old total_amount) to estimated_price
    Order.objects.all().update(estimated_price=models.F('final_price'))

class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0012_rename_total_amount_order_final_price_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_prices_backward_compatible, migrations.RunPython.noop),
    ]
