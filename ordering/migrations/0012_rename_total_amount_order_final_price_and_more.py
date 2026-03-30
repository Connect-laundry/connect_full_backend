from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0011_order_actual_weight_order_estimated_weight_and_more'), ]

    operations = [
        migrations.RenameField(
            model_name='order',
            old_name='total_amount',
            new_name='final_price',
        ),
        migrations.AlterField(
            model_name='order',
            name='payment_status',
            field=models.CharField(
                choices=[
                    ('PAID',
                     'Paid'),
                    ('UNPAID',
                     'Unpaid'),
                    ('PARTIALLY_PAID',
                     'Partially Paid'),
                    ('REFUNDED',
                     'Refunded')],
                default='UNPAID',
                max_length=20),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING',
                     'Pending'),
                    ('CONFIRMED',
                     'Confirmed'),
                    ('REJECTED',
                     'Rejected'),
                    ('PICKED_UP',
                     'Picked Up'),
                    ('WEIGHED',
                     'Weighed'),
                    ('AWAITING_FINAL_PAYMENT',
                     'Awaiting Final Payment'),
                    ('IN_PROCESS',
                     'In Process'),
                    ('OUT_FOR_DELIVERY',
                     'Out for Delivery'),
                    ('DELIVERED',
                     'Delivered'),
                    ('COMPLETED',
                     'Completed'),
                    ('CANCELLED',
                     'Cancelled')],
                default='PENDING',
                max_length=25),
        ),
    ]
