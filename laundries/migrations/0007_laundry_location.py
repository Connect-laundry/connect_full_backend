from django.db import migrations

# PostGIS-specific column. This must only run on PostgreSQL; the raw
# ``DO $$ ... $$`` block is invalid SQL on other backends (e.g. SQLite used by
# the test suite and local/CI setups), which would break ``migrate`` from
# scratch. We therefore guard on the connection vendor and no-op elsewhere.
# Databases that already applied this migration are unaffected (Django will not
# re-run it), and the net schema on PostgreSQL is identical.

_FORWARD_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='laundries_laundry' AND column_name='location'
        ) THEN
            ALTER TABLE laundries_laundry ADD COLUMN location geometry(Point, 4326);
            CREATE INDEX IF NOT EXISTS laundries_location_gist_idx ON laundries_laundry USING GIST (location);
        END IF;
    END IF;
END $$;
"""

_REVERSE_SQL = """
ALTER TABLE laundries_laundry DROP COLUMN IF EXISTS location;
"""


def add_postgis_location(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(_FORWARD_SQL)


def drop_postgis_location(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(_REVERSE_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0006_remove_laundry_laundries_l_latitud_13d2ca_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(add_postgis_location, drop_postgis_location),
    ]
