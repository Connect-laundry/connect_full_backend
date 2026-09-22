"""Create the shared rate-limit table used when there is no Redis.

settings.CACHES['throttle'] is a DatabaseCache on 'simame_throttle_cache' in
that case. createcachetable is idempotent and only creates tables for
DatabaseCache aliases, so this is a no-op with Redis or in tests.
"""
from django.core.management import call_command
from django.db import migrations


def create_cache_tables(apps, schema_editor):
    call_command('createcachetable', database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0013_user_auth_provider_user_clerk_created_at_and_more'),
    ]

    operations = [
        migrations.RunPython(create_cache_tables, migrations.RunPython.noop),
    ]
