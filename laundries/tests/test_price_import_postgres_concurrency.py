"""Real concurrency proof for price-list import confirmation and upload.

PostgreSQL only: SQLite has no row locking, so a threaded test there proves
nothing. Threads start behind a barrier, each with its own DB connection, and
go through the real endpoint with real commits.

    pytest --ds=<postgres settings> laundries/tests/test_price_import_postgres_concurrency.py
"""
import threading
from decimal import Decimal

import pytest
from django.db import connection
from django.urls import reverse

from laundries.models.price_import import PriceListDraftItem, PriceListImportJob
from laundries.models.pricing import LaundryPricingItem, LaundryWeightPricing

from .price_import_helpers import AI_SETTINGS, client_for, laundry_for, owner

pytestmark = [
    pytest.mark.skipif(connection.vendor != 'postgresql', reason='needs PostgreSQL row locking'),
    pytest.mark.django_db(transaction=True),
]

N = 12


@pytest.fixture(autouse=True)
def _ai(settings):
    for k, v in AI_SETTINGS.items():
        setattr(settings, k, v)
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, 'DEFAULT_THROTTLE_CLASSES': []}


def _race(fn):
    barrier = threading.Barrier(N)
    results, errors = [], []

    def worker():
        try:
            barrier.wait()
            results.append(fn())
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    return results


def test_concurrent_confirms_import_exactly_once():
    user = owner()
    laundry = laundry_for(user)
    job = PriceListImportJob.objects.create(laundry=laundry, status='READY', currency='GHS')
    draft = PriceListDraftItem.objects.create(job=job, item_name='Shirt', suggested_price=Decimal('15.00'),
                                              pricing_method='PER_ITEM')
    body = {'items': [
        {'draft_id': str(draft.id), 'action': 'CREATE', 'pricing_method': 'PER_ITEM',
         'item_name': 'Shirt', 'unit_price': '15.00'},
        {'action': 'CREATE', 'pricing_method': 'PER_KG', 'price_per_kg': '18.00'},
    ]}
    url = reverse('dashboard-price-imports-confirm', kwargs={'pk': job.id})

    statuses = _race(lambda: client_for(user).post(url, body, format='json').status_code)

    assert set(statuses) == {200}
    assert LaundryPricingItem.objects.filter(laundry=laundry).count() == 1
    assert LaundryWeightPricing.objects.filter(laundry=laundry).count() == 1
    job.refresh_from_db()
    assert job.status == 'CONFIRMED' and job.confirm_result['created'] == ['Shirt', 'Per-kg price']


def test_async_upload_extracts_in_a_real_background_thread(settings, monkeypatch):
    """202 comes back before extraction; the thread (own DB connection)
    finishes the job, and polling sees READY with drafts."""
    import time
    from .price_import_helpers import GeminiScript, _good_extraction_for_tests, fake_gemini_response, upload

    settings.PRICE_LIST_BACKGROUND_MODE = 'thread'
    GeminiScript(fake_gemini_response(_good_extraction_for_tests())).install(monkeypatch)
    user = owner()
    laundry_for(user)
    c = client_for(user)
    resp = c.post(reverse('dashboard-price-imports-list') + '?async=1', {'source_image': upload()}, format='multipart')
    assert resp.status_code == 202          # returned without waiting for extraction
    detail = reverse('dashboard-price-imports-detail', kwargs={'pk': resp.data['id']})
    for _ in range(100):
        data = c.get(detail).data
        if data['status'] != 'PROCESSING':
            break
        time.sleep(0.05)
    assert data['status'] == 'READY' and len(data['draft_items']) == 2
