"""Verify Sign in with Apple revocation credentials without exposing them.

Usage (e.g. in the Render shell):  python manage.py check_apple_revocation_config
Prints only a status label and the non-secret client ID / key ID.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from users.services.apple_revocation import check_native_revocation_config


class Command(BaseCommand):
    help = 'Checks that Apple accepts the configured Sign in with Apple key for token revocation.'

    def handle(self, *args, **options):
        result = check_native_revocation_config()
        client_id = getattr(settings, 'APPLE_NATIVE_CLIENT_ID', '') or 'com.connectlaundry.app'
        key_id = getattr(settings, 'APPLE_KEY_ID', '') or '(unset)'
        self.stdout.write(f'client_id={client_id} key_id={key_id} team_id_set={bool(getattr(settings, "APPLE_TEAM_ID", ""))}')
        self.stdout.write(f'result={result}')
        if result != 'ok':
            raise CommandError(
                'Apple revocation is NOT ready. not_configured = set APPLE_TEAM_ID/APPLE_KEY_ID/APPLE_PRIVATE_KEY; '
                'apple_rejected:400:invalid_client = wrong team, key, private key or client ID '
                '(the key must have Sign in with Apple enabled for com.connectlaundry.app).')
        self.stdout.write(self.style.SUCCESS('Apple accepted the client credentials; revocation can work.'))
