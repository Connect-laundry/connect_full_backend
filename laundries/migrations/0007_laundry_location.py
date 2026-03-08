from django.db import migrations
import os

def check_postgis(apps, schema_editor):
    # This is a no-op but allows us to branch in the SQL if needed
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('laundries', '0006_remove_laundry_laundries_l_latitud_13d2ca_idx_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            # SQL to run
            sql="""
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
            """,
            # SQL to run for rollback (reverse)
            reverse_sql="""
            ALTER TABLE laundries_laundry DROP COLUMN IF EXISTS location;
            """
        ),
    ]
