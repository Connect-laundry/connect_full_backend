# pyre-ignore[missing-module]
from django.db import migrations

# pyre-ignore[missing-module]
import os


def add_postgis_location(apps, schema_editor):
    """Add PostGIS location column if running on PostgreSQL with PostGIS."""
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        # Skip on SQLite and other non-PostgreSQL backends
        return

    with connection.cursor() as cursor:
        # Check if PostGIS is available
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
        if not cursor.fetchone():
            return

        # Check if column already exists
        cursor.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name='laundries_laundry' AND column_name='location'
        """)
        if cursor.fetchone():
            return

        # Add the column and index
        cursor.execute(
            "ALTER TABLE laundries_laundry ADD COLUMN location geometry(Point, 4326)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS laundries_location_gist_idx "
            "ON laundries_laundry USING GIST (location)"
        )


def remove_postgis_location(apps, schema_editor):
    """Remove location column (reverse migration)."""
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return

    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE laundries_laundry DROP COLUMN IF EXISTS location")


class Migration(migrations.Migration):

    dependencies = [
        ("laundries", "0006_remove_laundry_laundries_l_latitud_13d2ca_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(
            add_postgis_location,
            reverse_code=remove_postgis_location,
        ),
    ]
