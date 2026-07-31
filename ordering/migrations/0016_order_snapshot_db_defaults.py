"""Give the new order columns database-level defaults.

Django applies field defaults in Python, so a migration that adds a NOT NULL
column drops the database default straight after creating it. That is fine when
schema and code deploy together. It is not fine here: this database is shared
with a running backend that predates these columns, and its INSERTs do not name
them, so every order creation failed the not-null constraint.

Restoring the defaults makes the schema tolerant of both the old code and the
new. Django still sends explicit values on every write, so these defaults are
never used by current code — they exist purely so an older release can keep
serving while a deploy rolls out.
"""

from django.db import migrations

COLUMN_DEFAULTS = [
    ('items_total', '0'),
    ('pickup_fee', '0'),
    ('delivery_fee', '0'),
    ('discount_amount', '0'),
    ('tax_amount', '0'),
    ('platform_fee', '0'),
    ('currency', "'GHS'"),
    ('delivery_fees_in_app', 'false'),
    ('handover_code', "''"),
    ('delivery_confirmed_by_code', 'false'),
]

SET_DEFAULTS = [
    f'ALTER TABLE "ordering_order" ALTER COLUMN "{column}" SET DEFAULT {value};'
    for column, value in COLUMN_DEFAULTS
]

DROP_DEFAULTS = [
    f'ALTER TABLE "ordering_order" ALTER COLUMN "{column}" DROP DEFAULT;'
    for column, _ in COLUMN_DEFAULTS
]


class Migration(migrations.Migration):

    dependencies = [
        ('ordering', '0015_order_delivery_confirmed_by_code_order_handover_code'),
    ]

    operations = [
        migrations.RunSQL(sql=SET_DEFAULTS, reverse_sql=DROP_DEFAULTS),
    ]
