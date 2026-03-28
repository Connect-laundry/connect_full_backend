from django.db import migrations

def migrate_order_total(apps, schema_editor):
    Order = apps.get_model('ordering', 'Order')
    for order in Order.objects.all():
        if order.total_amount:
            # For existing orders, ensure estimated and final are populated
            if not order.estimated_price:
                order.estimated_price = order.total_amount
            if not order.final_price:
                order.final_price = order.total_amount
            order.save()

class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0012_rename_total_amount_order_final_price_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_order_total),
    ]
