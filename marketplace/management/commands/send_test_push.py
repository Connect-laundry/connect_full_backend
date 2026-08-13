"""Send a real push to a user's devices and report what Expo did with it.

Unlike ``validate_notifications`` (which short-circuits Expo), this command
performs a live send and then polls the receipt endpoint, so it answers the
only question that matters when debugging: *did the phone actually get it?*

    python manage.py send_test_push --email someone@example.com
    python manage.py send_test_push --token ExponentPushToken[xxxxxxxx]
    python manage.py send_test_push --email someone@example.com --wait 20
"""
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from marketplace.models import PushDevice
from marketplace.tasks import (
    EXPO_PUSH_URL,
    expo_push_headers,
    fetch_push_receipts,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Send a live test push notification and report Expo tickets/receipts."

    def add_arguments(self, parser):
        parser.add_argument('--email', help='Send to every active device of this user.')
        parser.add_argument('--token', help='Send to one Expo push token directly.')
        parser.add_argument('--title', default='Simame')
        parser.add_argument('--body', default='Test push — if you can see this, push is working.')
        parser.add_argument(
            '--wait', type=int, default=10,
            help='Seconds to wait before fetching delivery receipts (default 10).',
        )

    def handle(self, *args, **options):
        import requests

        tokens = self._resolve_tokens(options)
        self.stdout.write(f"Targets: {len(tokens)} token(s)")
        for token in tokens:
            self.stdout.write(f"  - {token[:28]}…")

        # Configuration surface — the two settings that silently break push.
        has_access_token = bool(getattr(settings, 'EXPO_ACCESS_TOKEN', ''))
        self.stdout.write(f"EXPO_PUSH_ENABLED : {getattr(settings, 'EXPO_PUSH_ENABLED', False)}")
        self.stdout.write(f"EXPO_ACCESS_TOKEN : {'set' if has_access_token else 'NOT SET'}")
        if not has_access_token:
            self.stdout.write(self.style.WARNING(
                "  If 'Enhanced Security for Push Notifications' is enabled on the Expo\n"
                "  project, sends without an access token are rejected."
            ))

        messages = [
            {
                "to": token,
                "sound": "default",
                "title": options['title'],
                "body": options['body'],
                "data": {"type": "SYSTEM", "test": True},
            }
            for token in tokens
        ]

        self.stdout.write("\nPOST " + EXPO_PUSH_URL)
        response = requests.post(
            EXPO_PUSH_URL, json=messages, headers=expo_push_headers(), timeout=15,
        )
        self.stdout.write(f"HTTP {response.status_code}")

        if response.status_code >= 400:
            self.stdout.write(self.style.ERROR(response.text[:1000]))
            raise CommandError("Expo rejected the send. See the response body above.")

        tickets = response.json().get('data', [])
        ticket_ids = []
        errors = 0
        for token, ticket in zip(tokens, tickets):
            if not isinstance(ticket, dict):
                continue
            if ticket.get('status') == 'error':
                errors += 1
                code = (ticket.get('details') or {}).get('error')
                self.stdout.write(self.style.ERROR(
                    f"  ticket ERROR [{token[:22]}…] {code}: {ticket.get('message')}"
                ))
            else:
                ticket_ids.append(ticket.get('id'))
                self.stdout.write(self.style.SUCCESS(
                    f"  ticket ok    [{token[:22]}…] id={ticket.get('id')}"
                ))

        if not ticket_ids:
            raise CommandError("No message was accepted by Expo.")

        wait = options['wait']
        self.stdout.write(f"\nWaiting {wait}s for delivery receipts…")
        time.sleep(wait)

        try:
            receipts = fetch_push_receipts(ticket_ids)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Could not fetch receipts: {exc}"))
            return

        if not receipts:
            self.stdout.write(self.style.WARNING(
                "No receipts yet — Expo can take a few minutes. Re-run with a longer --wait."
            ))
            return

        failures = 0
        for ticket_id, receipt in receipts.items():
            status_value = (receipt or {}).get('status')
            if status_value == 'ok':
                self.stdout.write(self.style.SUCCESS(f"  receipt ok   {ticket_id}"))
                continue
            failures += 1
            code = ((receipt or {}).get('details') or {}).get('error')
            self.stdout.write(self.style.ERROR(
                f"  receipt FAIL {ticket_id} {code}: {(receipt or {}).get('message')}"
            ))

        if failures:
            raise CommandError(
                f"{failures} receipt(s) failed. 'DeviceNotRegistered' means the token is stale; "
                "'MessageRateExceeded' means slow down; a credentials error means the APNs key "
                "or FCM config is missing in EAS."
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nAll {len(receipts)} message(s) delivered to the push service."
        ))

    def _resolve_tokens(self, options):
        token = options.get('token')
        email = options.get('email')

        if token:
            return [token]

        if not email:
            raise CommandError("Provide --email or --token.")

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {email}.") from exc

        tokens = list(
            PushDevice.objects.filter(user=user, is_active=True)
            .values_list('token', flat=True)
        )
        if not tokens:
            raise CommandError(
                f"{email} has no active push devices. The app registers one on first "
                "authenticated launch of a dev-client or release build (never Expo Go)."
            )
        return tokens
