"""Validate every external dependency and critical setting before/after deploy.

Usage:
    python manage.py validate_environment              # config + live checks
    python manage.py validate_environment --no-network # config checks only

Each check reports PASS / WARNING / FAIL. The command exits non-zero if any
check FAILs, so it can gate a deploy pipeline. WARNINGs mean the feature is
disabled/degraded but the API will still serve (graceful degradation).
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

PASS = 'PASS'
WARN = 'WARNING'
FAIL = 'FAIL'


class Command(BaseCommand):
    help = 'Validate external integrations and environment configuration.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-network', action='store_true',
            help='Skip live connectivity probes (config presence checks only).',
        )

    def handle(self, *args, **options):
        self.network = not options['no_network']
        results = []
        for name, check in [
            ('Secret keys', self.check_secret_key),
            ('Database', self.check_database),
            ('Media storage / Cloudinary', self.check_cloudinary),
            ('Redis cache', self.check_redis_cache),
            ('Celery broker', self.check_celery),
            ('Paystack', self.check_paystack),
            ('Expo push', self.check_expo),
            ('Sentry', self.check_sentry),
            ('Weather promo', self.check_weather),
            ('Email (SMTP)', self.check_email),
            ('Geocoding', self.check_geocoding),
        ]:
            try:
                status, detail = check()
            except Exception as exc:  # a check itself must never crash the run
                status, detail = FAIL, f'check raised: {exc}'
            results.append((name, status, detail))

        width = max(len(name) for name, _, _ in results)
        styles = {
            PASS: self.style.SUCCESS,
            WARN: self.style.WARNING,
            FAIL: self.style.ERROR,
        }
        self.stdout.write('')
        for name, status, detail in results:
            line = f'{name.ljust(width)}  {status.ljust(7)}  {detail}'
            self.stdout.write(styles[status](line))
        self.stdout.write('')

        failures = [name for name, status, _ in results if status == FAIL]
        warnings = [name for name, status, _ in results if status == WARN]
        summary = f'{len(results)} checks: {len(failures)} FAIL, {len(warnings)} WARNING'
        if failures:
            self.stderr.write(self.style.ERROR(summary))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(summary))

    # ------------------------------------------------------------------ checks

    def check_secret_key(self):
        key = settings.SECRET_KEY or ''
        if not key:
            return FAIL, 'SECRET_KEY is not set.'
        if not settings.DEBUG and len(key) < 50:
            return FAIL, 'Production SECRET_KEY must be at least 50 characters.'
        if settings.DEBUG:
            return WARN, 'DEBUG=True — never run production with DEBUG enabled.'
        return PASS, 'SECRET_KEY set; DEBUG off.'

    def check_database(self):
        if not self.network:
            return PASS, 'configured (connectivity not probed).'
        from django.db import connections
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT 1')
            return PASS, 'connection OK.'
        except Exception as exc:
            return FAIL, f'cannot connect: {exc}'

    def check_cloudinary(self):
        cloud = os.getenv('CLOUDINARY_CLOUD_NAME')
        key = os.getenv('CLOUDINARY_API_KEY')
        secret = os.getenv('CLOUDINARY_API_SECRET')
        backend = settings.STORAGES['default']['BACKEND']
        if not all([cloud, key, secret]):
            missing = [n for n, v in [
                ('CLOUDINARY_CLOUD_NAME', cloud),
                ('CLOUDINARY_API_KEY', key),
                ('CLOUDINARY_API_SECRET', secret),
            ] if not v]
            level = WARN if settings.DEBUG else FAIL
            return level, (
                f'missing {", ".join(missing)} — media falls back to '
                f'filesystem storage ({backend}).'
            )
        if 'cloudinary' not in backend.lower():
            return WARN, f'credentials set but active backend is {backend}.'
        if not self.network:
            return PASS, 'credentials configured.'
        try:
            import cloudinary.api
            cloudinary.api.ping()
            return PASS, 'credentials valid; API reachable.'
        except Exception as exc:
            return FAIL, f'credentials configured but ping failed: {exc}'

    def check_redis_cache(self):
        backend = settings.CACHES['default']['BACKEND']
        if 'redis' not in backend.lower():
            level = WARN if not settings.DEBUG else PASS
            return level, f'using {backend} (no Redis) — fine for dev, not for multi-worker prod.'
        if not self.network:
            return PASS, 'Redis cache configured.'
        from django.core.cache import cache
        try:
            cache.set('validate_environment_probe', '1', 10)
            value = cache.get('validate_environment_probe')
            if value != '1':
                # IGNORE_EXCEPTIONS swallows connection errors — a failed
                # round-trip means Redis is actually unreachable.
                return FAIL, 'Redis configured but round-trip failed (server unreachable?).'
            return PASS, 'round-trip OK.'
        except Exception as exc:
            return FAIL, f'cache error: {exc}'

    def check_celery(self):
        broker = settings.CELERY_BROKER_URL
        if settings.CELERY_TASK_ALWAYS_EAGER:
            return WARN, 'CELERY_TASK_ALWAYS_EAGER=True — tasks run inline (dev mode).'
        if not broker:
            return FAIL, 'CELERY_BROKER_URL is not set.'
        if not self.network:
            return PASS, f'broker configured ({broker.split("@")[-1]}).'
        try:
            from config.celery import app as celery_app
            with celery_app.connection_or_acquire() as conn:
                conn.ensure_connection(max_retries=0, timeout=5)
            return PASS, 'broker reachable.'
        except Exception as exc:
            return FAIL, f'broker unreachable: {exc}'

    def check_paystack(self):
        secret = settings.PAYSTACK_SECRET_KEY
        if not secret:
            return FAIL, 'PAYSTACK_SECRET_KEY is not set — payments and webhooks will be rejected.'
        if not secret.startswith('sk_'):
            return WARN, 'PAYSTACK_SECRET_KEY does not look like a Paystack secret key (sk_...).'
        live = secret.startswith('sk_live_')
        if not settings.DEBUG and not live:
            return WARN, 'using a TEST key in production mode.'
        return PASS, f'{"live" if live else "test"} key configured.'

    def check_expo(self):
        if not settings.EXPO_PUSH_ENABLED:
            return WARN, 'EXPO_PUSH_ENABLED=False — pushes are recorded as SKIPPED.'
        return PASS, 'push delivery enabled.'

    def check_sentry(self):
        dsn = os.getenv('SENTRY_DSN', '')
        if not dsn or not dsn.startswith('http') or 'your_real_dsn' in dsn:
            return WARN, 'SENTRY_DSN not configured — no error monitoring.'
        if os.getenv('DISABLE_SENTRY', 'False') == 'True':
            return WARN, 'DISABLE_SENTRY=True — monitoring disabled.'
        return PASS, 'DSN configured.'

    def check_weather(self):
        if not settings.WEATHER_PROMO_ENABLED:
            return PASS, 'weather promos disabled (opt-in feature).'
        if not settings.WEATHER_PROMO_LATITUDE or not settings.WEATHER_PROMO_LONGITUDE:
            return FAIL, 'WEATHER_PROMO_ENABLED but coordinates are missing.'
        return PASS, 'enabled with coordinates.'

    def check_email(self):
        host = settings.EMAIL_HOST
        user = settings.EMAIL_HOST_USER
        password = settings.EMAIL_HOST_PASSWORD
        if not host:
            level = WARN if settings.DEBUG else FAIL
            return level, 'EMAIL_HOST not set — password reset emails cannot be sent.'
        if not user or not password:
            return WARN, 'EMAIL_HOST set but credentials incomplete.'
        if not self.network:
            return PASS, f'SMTP configured ({host}).'
        try:
            from django.core.mail import get_connection
            conn = get_connection(timeout=10)
            conn.open()
            conn.close()
            return PASS, f'SMTP connection to {host} OK.'
        except Exception as exc:
            return FAIL, f'SMTP connection failed: {exc}'

    def check_geocoding(self):
        provider = settings.GEOCODING_PROVIDER
        if not provider:
            return WARN, 'GEOCODING_PROVIDER unset — address-only onboarding requires coordinates.'
        if provider == 'google' and not settings.GOOGLE_MAPS_API_KEY:
            return FAIL, 'GEOCODING_PROVIDER=google but GOOGLE_MAPS_API_KEY missing.'
        if provider == 'mapbox' and not settings.MAPBOX_ACCESS_TOKEN:
            return FAIL, 'GEOCODING_PROVIDER=mapbox but MAPBOX_ACCESS_TOKEN missing.'
        return PASS, f'{provider} configured.'
