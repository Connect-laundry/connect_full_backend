"""Database-level defaults for the settlement columns.

Same reason as ordering/0016: this database is shared with a running backend
that predates these columns, and its INSERTs omit them. Without a default the
not-null constraint rejects the write.
"""

from django.db import migrations

STATEMENTS = [
    ('payments_payment', 'settled_directly', 'false'),
    ('payments_ordersettlement', 'route', "'PLATFORM'"),
]

SET_DEFAULTS = [
    f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET DEFAULT {value};'
    for table, column, value in STATEMENTS
]

DROP_DEFAULTS = [
    f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP DEFAULT;'
    for table, column, _ in STATEMENTS
]


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0010_ordersettlement_release_after'),
    ]

    operations = [
        migrations.RunSQL(sql=SET_DEFAULTS, reverse_sql=DROP_DEFAULTS),
    ]
