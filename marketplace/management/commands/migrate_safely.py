"""Run migrations behind a PostgreSQL advisory lock.

Containers run migrations at boot, so a rolling deploy or a scale-up starts
several instances at once and they race the same migration. Django does not
serialise this for you: the usual symptoms are duplicate-column errors, a
half-applied migration, or a deadlock that takes the deploy down.

An advisory lock is held for the duration of the migrate, so the first
instance applies the migrations and the rest wait and then find nothing to do.
The lock lives in PostgreSQL, so it works across instances — unlike anything
cache-based.

    python manage.py migrate_safely
    python manage.py migrate_safely --lock-timeout 600
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

# Arbitrary but fixed: every instance must ask for the same lock.
MIGRATION_LOCK_ID = 8472113


class Command(BaseCommand):
    help = "Run `migrate` while holding a PostgreSQL advisory lock."

    def add_arguments(self, parser):
        parser.add_argument(
            '--lock-timeout', type=int, default=300,
            help='Seconds to wait for the lock before giving up (default 300).',
        )

    def handle(self, *args, **options):
        vendor = connection.vendor
        if vendor != 'postgresql':
            # SQLite and friends have no advisory locks, and local dev has no
            # concurrent deploys to protect against.
            self.stdout.write(f"{vendor} has no advisory locks; migrating directly.")
            call_command('migrate', '--noinput')
            return

        timeout_ms = max(1, options['lock_timeout']) * 1000

        with connection.cursor() as cursor:
            # Bound the wait so a stuck peer fails the deploy loudly rather
            # than hanging the container forever.
            cursor.execute("SET lock_timeout = %s", [f"{timeout_ms}ms"])
            self.stdout.write("Waiting for the migration lock...")
            cursor.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_LOCK_ID])

        try:
            self.stdout.write(self.style.SUCCESS("Lock acquired; running migrations."))
            call_command('migrate', '--noinput')
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID])
            self.stdout.write("Migration lock released.")
