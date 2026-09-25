"""Non-destructive live probe of the price-list AI providers.

    python manage.py price_import_probe            # config + both providers
    python manage.py price_import_probe --config-only

Run once per environment after setting or rotating keys (staging first, then
ONE production run). It renders a small synthetic price list in memory, sends
it to each configured provider, and checks:

  * authentication succeeds with the configured key
  * the model call and image input succeed
  * Gemini structured output validates against the Pydantic schema
  * OCR.space Engine 3 returns ParsedText

It writes nothing to the database and never prints key values: only whether
each key is set and a short non-reversible fingerprint (SHA-256 prefix). Use
the fingerprint to confirm which key an environment uses without exposing it.
"""
import hashlib
import io
import json
import os
import time

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFont

from laundries.services.price_import.errors import ProviderError
from laundries.services.price_import.image import prepare_image
from laundries.services.price_import.providers import get_provider
from laundries.services.price_import.validation import validate_extraction

EXPECTED = {'Shirt': '15.00', 'Trouser': '20.00'}


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10] if value else '-'


def _probe_image():
    img = Image.new('RGB', (1000, 520), 'white')
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('arial.ttf', 40)
    except OSError:
        font = ImageFont.load_default(size=40)
    for i, line in enumerate(['SIMAME PROBE LAUNDRY', 'Shirt ........ GH¢ 15', 'Trouser ...... GH¢ 20',
                              'Wash & Fold ... 18/kg']):
        d.text((50, 50 + i * 110), line, fill='black', font=font)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=92)
    return SimpleUploadedFile('probe.jpg', buf.getvalue(), content_type='image/jpeg')


class Command(BaseCommand):
    help = 'Live, non-destructive probe of Gemini and OCR.space for price-list import.'

    def add_arguments(self, parser):
        parser.add_argument('--config-only', action='store_true')
        parser.add_argument('--provider', choices=['gemini', 'ocr_space'], default=None)

    def handle(self, *args, **opts):
        report = {
            'environment': getattr(settings, 'SENTRY_ENVIRONMENT', '') or 'unknown',
            'feature_enabled': settings.PRICE_LIST_AI_ENABLED,
            'allowlist': settings.PRICE_LIST_AI_LAUNDRY_ALLOWLIST,
            'primary': settings.PRICE_LIST_PRIMARY_PROVIDER,
            'fallback': settings.PRICE_LIST_FALLBACK_PROVIDER,
            'gemini_model': settings.GEMINI_MODEL,
            'gemini_fallback_model': settings.GEMINI_FALLBACK_MODEL,
            'gemini_key_set': bool(settings.GEMINI_API_KEY),
            'gemini_key_type': ('auth key (AQ.)' if settings.GEMINI_API_KEY.startswith('AQ.')
                                else 'standard key (AIza)' if settings.GEMINI_API_KEY.startswith('AIza')
                                else 'unknown' if settings.GEMINI_API_KEY else '-'),
            'gemini_key_fingerprint': fingerprint(settings.GEMINI_API_KEY),
            'ocr_key_set': bool(settings.OCR_SPACE_API_KEY),
            'ocr_key_fingerprint': fingerprint(settings.OCR_SPACE_API_KEY),
            'ocr_engine': settings.OCR_SPACE_ENGINE,
            # The SDK prefers GOOGLE_API_KEY from env over GEMINI_API_KEY. We
            # always pass the key explicitly, but a leftover old key here is a
            # rotation smell and must be removed.
            'stray_GOOGLE_API_KEY_in_env': bool(os.environ.get('GOOGLE_API_KEY')),
            'checks': {},
        }
        ok = True
        if report['stray_GOOGLE_API_KEY_in_env']:
            report['warnings'] = ['GOOGLE_API_KEY is set in this environment; remove it (old Gemini key?)']
        if not opts['config_only']:
            names = [opts['provider']] if opts['provider'] else ['gemini', 'ocr_space']
            image = prepare_image(_probe_image())
            for name in names:
                check = self._probe(name, image)
                report['checks'][name] = check
                ok = ok and check.get('ok', False)
        self.stdout.write(json.dumps(report, indent=1))
        self.stdout.write('PROBE ' + ('PASS' if ok else 'FAIL'))

    @staticmethod
    def _probe(name, image):
        provider = get_provider(name)
        if provider is None or not provider.is_configured():
            return {'ok': False, 'error': 'NOT_CONFIGURED'}
        started = time.monotonic()
        try:
            result = provider.extract(image, timeout=60)
        except ProviderError as err:
            return {'ok': False, 'error': err.kind, 'status': err.status_code,
                    'attempts': getattr(err, 'attempts', None),
                    'ms': int((time.monotonic() - started) * 1000)}
        cands, doc, currency = validate_extraction(result.extraction, trusted_source=result.structured)
        found = {c.item_name: str(c.price) for c in cands if c.pricing_method == 'PER_ITEM'}
        kg = [str(c.price_per_kg) for c in cands if c.pricing_method == 'PER_KG']
        prices_ok = all(found.get(k) == v for k, v in EXPECTED.items())
        return {
            'ok': prices_ok,
            'model': result.model,
            'auth': 'ok',
            'image_input': 'ok',
            'schema_valid': True,
            'ms': result.latency_ms,
            'items': found,
            'per_kg': kg,
            'currency': currency,
            'usage': {k: v for k, v in result.usage.items() if k != 'attempts'},
            'model_attempts': result.usage.get('attempts'),
        }
