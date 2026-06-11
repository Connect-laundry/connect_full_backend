import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0002_order_cancellation_reason_order_cancelled_at_and_more'),
    ]

    operations = [
        # Drop the explicit index on `expires_at` BEFORE renaming the field.
        # On SQLite the later AddField operations remake the whole table and
        # rebuild every index from state; if this index still references the
        # about-to-be-renamed `expires_at` column the remake raises
        # FieldDoesNotExist. PostgreSQL never remakes the table, which is why
        # production applied this migration cleanly. (This index is removed in
        # 0004 on existing databases; doing it here is schema-equivalent.)
        migrations.RemoveIndex(
            model_name='coupon',
            name='ordering_co_expires_e55a2f_idx',
        ),
        migrations.RenameField(
            model_name='coupon',
            old_name='used_count',
            new_name='current_usage',
        ),
        migrations.RenameField(
            model_name='coupon',
            old_name='usage_limit',
            new_name='max_usage',
        ),
        migrations.RenameField(
            model_name='coupon',
            old_name='min_order_amount',
            new_name='min_order_value',
        ),
        migrations.RenameField(
            model_name='coupon',
            old_name='expires_at',
            new_name='valid_to',
        ),
        migrations.AddField(
            model_name='coupon',
            name='valid_from',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name='coupon',
            name='user_limit',
            field=models.PositiveIntegerField(default=1, help_text='Times a single user can use this coupon.'),
        ),
        migrations.AlterField(
            model_name='coupon',
            name='applicable_laundries',
            field=models.ManyToManyField(blank=True, related_name='coupons', to='laundries.laundry'),
        ),
        migrations.AlterField(
            model_name='coupon',
            name='max_discount_amount',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Maximum discount allowed (for percentage type).', max_digits=10, null=True),
        ),
        migrations.CreateModel(
            name='CouponUsage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('used_at', models.DateTimeField(auto_now_add=True)),
                ('coupon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='usages', to='ordering.coupon')),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='coupon_usage', to='ordering.order')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='coupon_usages', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('user', 'coupon', 'order')},
            },
        ),
    ]
