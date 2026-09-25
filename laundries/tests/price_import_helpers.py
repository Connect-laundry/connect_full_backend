"""Shared fakes for price-list import tests. No test here calls a paid API."""
import io
import json
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.services.price_import.providers import gemini as gemini_mod
from laundries.services.price_import.providers import ocr_space as ocr_mod
from laundries.services.price_import.schema import PriceListExtraction
from users.models import User

AI_SETTINGS = dict(
    PRICE_LIST_AI_ENABLED=True,
    PRICE_LIST_AI_LAUNDRY_ALLOWLIST=[],
    GEMINI_API_KEY='test-gemini-key-not-real',
    OCR_SPACE_API_KEY='test-ocr-key-not-real',
    PRICE_LIST_SHADOW_CROSSCHECK=False,
    PRICE_LIST_DAILY_LIMIT_PER_LAUNDRY=15,
    PRICE_LIST_MAX_CONCURRENT=1,
    PRICE_LIST_PRIMARY_PROVIDER='gemini',
    PRICE_LIST_FALLBACK_PROVIDER='ocr_space',
    PRICE_LIST_GEMINI_ENABLED=True,
    PRICE_LIST_OCR_ENABLED=True,
)


def owner(email='owner-pi@example.com', phone='233500070001'):
    return User.objects.create_user(email=email, phone=phone, password='StrongPass123!', role=User.Role.OWNER)


def customer(email='cust-pi@example.com', phone='233500070009'):
    return User.objects.create_user(email=email, phone=phone, password='StrongPass123!', role=User.Role.CUSTOMER)


def client_for(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def laundry_for(user, name='Import Laundry', phone='0240000070'):
    return Laundry.objects.create(
        owner=user, name=name, address='x', city='Accra',
        latitude='5.6', longitude='-0.18', phone_number=phone,
    )


def image_bytes(fmt='JPEG', size=(900, 700), seed=0, text=True):
    img = Image.new('RGB', size, (255, 255, 255 - (seed % 50)))
    if text:
        d = ImageDraw.Draw(img)
        for i, line in enumerate(['PRICE LIST', f'Shirt .... 15 #{seed}', 'Trouser .... 20']):
            d.text((40, 40 + i * 60), line, fill='black')
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def upload(fmt='JPEG', name=None, seed=0, content_type=None, data=None):
    ext = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[fmt]
    ctype = content_type or {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}[fmt]
    return SimpleUploadedFile(name or f'pricelist.{ext}', data or image_bytes(fmt, seed=seed), content_type=ctype)


def item(raw_name, price=None, method='PER_ITEM', source=None, confidence=0.95, **kw):
    row = {
        'raw_name': raw_name, 'normalized_name': kw.pop('normalized_name', raw_name),
        'category': kw.pop('category', None), 'variant': kw.pop('variant', None),
        'pricing_method': method, 'price': price if method != 'PER_KG' else None,
        'price_per_kg': price if method == 'PER_KG' else None,
        'surcharge_type': None, 'surcharge_amount': None,
        'source_text': source if source is not None else f'{raw_name} .... GH¢ {price}',
        'confidence': confidence, 'warnings': kw.pop('warnings', []),
    }
    row.update(kw)
    return row


def extraction(items, currency='GHS', warnings=None, document_confidence=0.95):
    return PriceListExtraction.model_validate({
        'currency': currency, 'business_name': None, 'items': items,
        'warnings': warnings or [], 'document_confidence': document_confidence,
    })


def fake_gemini_response(ext: PriceListExtraction, parsed=True):
    return SimpleNamespace(
        parsed=ext if parsed else None,
        text=ext.model_dump_json(),
        candidates=[],
        usage_metadata=SimpleNamespace(prompt_token_count=1200, candidates_token_count=300,
                                       thoughts_token_count=50, total_token_count=1550),
    )


def genai_error(code, status, quota_id=None):
    from google.genai import errors
    cls = errors.ServerError if code >= 500 else errors.ClientError
    body = {'error': {'code': code, 'message': f'simulated {status}', 'status': status}}
    if quota_id:
        body['error']['details'] = [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                                     'violations': [{'quotaId': quota_id, 'quotaValue': '20'}]}]
    return cls(code, body)


class GeminiScript:
    """Patch GeminiPriceListProvider._call with scripted outcomes per call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    # Installed as a class attribute; an instance is not a descriptor, so the
    # provider's ``self`` is not passed.
    def __call__(self, model, image, timeout):
        self.calls.append(model)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def install(self, monkeypatch):
        monkeypatch.setattr(gemini_mod.GeminiPriceListProvider, '_call', self)
        return self


class FakeHTTPResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


def ocr_ok(text):
    return FakeHTTPResponse(200, {
        'ParsedResults': [{'ParsedText': text, 'FileParseExitCode': 1}],
        'OCRExitCode': 1, 'IsErroredOnProcessing': False, 'ProcessingTimeInMilliseconds': '900',
    })


class OCRScript:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append({'url': url, 'headers': headers, 'data': data,
                           'size': len(files['file'][1]) if files else 0})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def install(self, monkeypatch):
        monkeypatch.setattr(ocr_mod.requests, 'post', self)
        return self


def _good_extraction_for_tests():
    return extraction([item('Shirt', '15'), item('Wash & Fold', '18', method='PER_KG', source='Wash & Fold 18/kg')])
