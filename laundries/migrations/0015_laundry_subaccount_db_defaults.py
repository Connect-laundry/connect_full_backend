"""Database-level defaults for the subaccount columns.

Same reason as ordering/0016: a running backend that predates these columns
writes laundries without naming them.
"""

from django.db import migrations

STATEMENTS = [
    ('paystack_subaccount_code', "''"),
    ('split_payments_enabled', 'false'),
]

SET_DEFAULTS = [
    f'ALTER TABLE "laundries_laundry" ALTER COLUMN "{column}" SET DEFAULT {value};'
    for column, value in STATEMENTS
]

DROP_DEFAULTS = [
    f'ALTER TABLE "laundries_laundry" ALTER COLUMN "{column}" DROP DEFAULT;'
    for column, _ in STATEMENTS
]


class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0014_laundry_paystack_subaccount_code_and_more'),
    ]

    operations = [
        migrations.RunSQL(sql=SET_DEFAULTS, reverse_sql=DROP_DEFAULTS),
    ]
