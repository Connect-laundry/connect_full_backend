from django.db import migrations, models

def migrate_order_total(apps, schema_editor):
    Order = apps.get_model('ordering', 'Order')
    # Copy final_price (the old total_amount) to estimated_price where missing
    for order in Order.objects.all():
        if not order.estimated_price and order.final_price:
            order.estimated_price = order.final_price
            order.save()

class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0012_rename_total_amount_order_final_price_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_order_total, migrations.RunPython.noop),
    ]
