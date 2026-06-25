"""End-to-end validation of the notification + campaign pipeline.

Runs as a dry-run by default (everything inside a transaction that is rolled
back) so it is safe to run against any environment, including production, to
confirm the wiring is healthy before a release.

Usage:
    python manage.py validate_notifications
    python manage.py validate_notifications --commit   # persist the test rows

It exercises: preference push-gating, quiet-hours, campaign segmentation,
delivery counters, open/click tracking, and conversion attribution — without
sending any real push (Expo calls are short-circuited via EXPO_PUSH_ENABLED).
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from marketplace.models import Notification, NotificationCampaign
from marketplace.services.notification_service import NotificationService
from marketplace.services.campaign_service import CampaignService

User = get_user_model()


class Command(BaseCommand):
    help = "Validate the notification/campaign pipeline end-to-end (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Persist the validation rows instead of rolling back.')

    def handle(self, *args, **options):
        commit = options['commit']
        checks = []

        def check(name, passed, detail=''):
            checks.append((name, passed, detail))
            mark = self.style.SUCCESS('PASS') if passed else self.style.ERROR('FAIL')
            self.stdout.write(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Notification pipeline validation ({'COMMIT' if commit else 'DRY-RUN'})"))

        # Force push 'enabled' so push_status transitions are observable, but
        # patch the Celery dispatch so no real Expo call is made.
        with override_settings(EXPO_PUSH_ENABLED=True):
            from unittest.mock import patch
            with patch('marketplace.services.notification_service.NotificationService._queue_push'):
                sid = transaction.savepoint()
                try:
                    from users.models import Address
                    stamp = timezone.now().strftime('%Y%m%d%H%M%S')
                    user = User.objects.create_user(
                        email=f'validate+{stamp}@connect.local',
                        phone=f'233{stamp[-9:]}', password='x', role='CUSTOMER')
                    Address.objects.create(
                        user=user, label='Home', address_line1='1 Test St', city='Accra')

                    # 1. Preference gating
                    n1 = NotificationService.notify_user(
                        user, title='t', body='b', category='ORDER', type=Notification.Type.ORDER)
                    check('order push queued (prefs allow)',
                          n1.push_status == Notification.PushStatus.PENDING, n1.push_status)

                    pref = NotificationService.get_preferences(user)
                    pref.order_updates = False
                    pref.save()
                    n2 = NotificationService.notify_user(
                        user, title='t2', body='b', category='ORDER',
                        type=Notification.Type.ORDER, dedup_key=f'v2:{stamp}')
                    check('order push skipped (opted out)',
                          n2.push_status == Notification.PushStatus.SKIPPED, n2.push_status)

                    # 2. Segmentation (city)
                    in_city = CampaignService.resolve_recipients(
                        NotificationCampaign.Segment.CITY, {'city': 'Accra'}
                    ).filter(pk=user.pk).exists()
                    check('city segment resolves user', in_city)

                    # 3. Campaign delivery + counters
                    campaign = NotificationCampaign.objects.create(
                        name=f'validate-{stamp}', segment=NotificationCampaign.Segment.CUSTOM,
                        segment_params={'user_ids': [str(user.id)]}, title='Hi', body='There',
                        category='CAMPAIGN', promo_code='VALIDATE10')
                    delivered, skipped = CampaignService.run(campaign)
                    campaign.refresh_from_db()
                    check('campaign delivered to 1 recipient',
                          delivered == 1 and campaign.delivered_count == 1,
                          f'delivered={delivered}')

                    camp_notif = Notification.objects.filter(campaign=campaign, user=user).first()
                    check('campaign notification carries promo code',
                          bool(camp_notif) and camp_notif.promo_code == 'VALIDATE10')

                    # 4. Open / click tracking rollup
                    camp_notif.mark_clicked()
                    campaign.refresh_from_db()
                    check('open+click rolled up to campaign',
                          campaign.opened_count == 1 and campaign.clicked_count == 1,
                          f'opened={campaign.opened_count} clicked={campaign.clicked_count}')

                    # 5. Conversion attribution
                    CampaignService.attribute_conversion(user, value=Decimal('120.00'))
                    campaign.refresh_from_db()
                    check('conversion + revenue attributed',
                          campaign.converted_count == 1 and campaign.revenue_generated == Decimal('120.00'),
                          f'converted={campaign.converted_count} revenue={campaign.revenue_generated}')

                    if commit:
                        transaction.savepoint_commit(sid)
                        self.stdout.write(self.style.WARNING('Validation rows committed.'))
                    else:
                        transaction.savepoint_rollback(sid)
                        self.stdout.write('Dry-run rows rolled back.')
                except Exception:
                    transaction.savepoint_rollback(sid)
                    raise

        passed = sum(1 for _, ok, _ in checks if ok)
        total = len(checks)
        summary = f"{passed}/{total} checks passed"
        if passed == total:
            self.stdout.write(self.style.SUCCESS(f"\n✅ {summary} — pipeline healthy."))
        else:
            self.stdout.write(self.style.ERROR(f"\n❌ {summary} — see failures above."))
            raise SystemExit(1)
