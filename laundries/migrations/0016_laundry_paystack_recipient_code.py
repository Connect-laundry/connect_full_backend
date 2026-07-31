from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0015_laundry_subaccount_db_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='laundry',
            name='paystack_recipient_code',
            field=models.CharField(
                blank=True, default='', max_length=100,
                verbose_name='paystack recipient code',
            ),
        ),
        migrations.RunSQL(
            # A backend release that predates this column writes laundries
            # without naming it, and NOT NULL with no database default rejects
            # that. See ordering/0016 for the full reasoning.
            sql=(
                'ALTER TABLE "laundries_laundry" '
                "ALTER COLUMN \"paystack_recipient_code\" SET DEFAULT '';"
            ),
            reverse_sql=(
                'ALTER TABLE "laundries_laundry" '
                'ALTER COLUMN "paystack_recipient_code" DROP DEFAULT;'
            ),
        ),
    ]
