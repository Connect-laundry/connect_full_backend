"""End-to-end import safety for new pricing methods; provider calls are mocked."""
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from laundries.models.price_import import PriceListImportJob
from laundries.models.pricing import BasketDefinition, LaundryPricingItem
from laundries.services.price_import import circuit
from laundries.services.price_import.documents import prepare_documents
from laundries.services.price_import.errors import ImageRejected
from laundries.services.price_import.text_parser import parse_text
from laundries.services.price_import.validation import validate_extraction
from .price_import_helpers import (
    AI_SETTINGS, GeminiScript, client_for, extraction, fake_gemini_response, item,
    laundry_for, owner, upload,
)


@pytest.fixture(autouse=True)
def import_settings(settings, monkeypatch, tmp_path):
    for key, value in AI_SETTINGS.items():
        setattr(settings, key, value)
    settings.UNIFIED_PRICING_ENABLED = True
    settings.PRICE_LIST_BACKGROUND_MODE = 'inline'
    monkeypatch.setattr(PriceListImportJob._meta.get_field('source_document'), 'storage', FileSystemStorage(location=tmp_path / 'private'))
    circuit._cache().clear()


def pdf_upload(pages=2):
    from reportlab.pdfgen import canvas
    output = io.BytesIO()
    doc = canvas.Canvas(output)
    for n in range(pages):
        doc.drawString(60, 700, f'Wash only - Basket {n + 1}: GHS 60')
        doc.showPage()
    doc.save()
    return SimpleUploadedFile('menu.pdf', output.getvalue(), content_type='application/pdf')


def test_pdf_pages_are_rasterized_and_bounded():
    result = prepare_documents([pdf_upload()])
    assert len(result.pages) == 2
    assert all(p.provider_mime == 'image/jpeg' and p.provider_bytes.startswith(b'\xff\xd8') for p in result.pages)
    with pytest.raises(ImageRejected, match='at most 5'):
        prepare_documents([pdf_upload(6)])


@pytest.mark.parametrize('data,content_type', [
    (b'%PDF-1.7 broken', 'application/pdf'),
    (b'<script>alert(1)</script>', 'application/pdf'),
    (b'MZ\x00\x01', 'image/jpeg'),
])
def test_invalid_documents_never_reach_a_provider(data, content_type):
    with pytest.raises(ImageRejected):
        prepare_documents([SimpleUploadedFile('price.pdf', data, content_type=content_type)])


def test_batch_size_and_combined_size_are_bounded(settings):
    with pytest.raises(ImageRejected):
        prepare_documents([upload()] * 6)
    settings.PRICE_LIST_UPLOAD_MAX_MB = 0
    with pytest.raises(ImageRejected):
        prepare_documents([upload(), upload(seed=2)])


def test_basket_capacity_is_never_invented():
    rows, _, _ = validate_extraction(extraction([
        item('Medium basket', '70', method='PER_BASKET', service_type='WASH_AND_IRON', basket_name='Medium'),
    ]), trusted_source=True)
    assert rows[0].pricing_details['basket_capacity'] is None
    assert 'BASKET_CAPACITY_REQUIRED' in rows[0].warnings
    assert rows[0].review_state == 'CHECK'


def test_ocr_keeps_flat_brackets_and_baskets_distinct_from_unit_rates():
    result = parse_text('WASH & IRON\nSmall basket .... GHS 60\n0–5 kg: GHS 50\nOver 5–10 kg: GHS 90\nWash and fold 15/kg')
    assert [r.pricing_method for r in result.items] == ['PER_BASKET', 'WEIGHT_TIER', 'WEIGHT_TIER', 'PER_KG']
    assert result.items[0].service_type == 'WASH_AND_IRON'
    assert result.items[1].price == '50'
    assert result.items[1].weight_min == '0'
    assert result.items[2].weight_min == '5'
    assert result.items[1].price_per_kg is None


def test_service_groups_do_not_deduplicate_each_other():
    candidates, _, _ = validate_extraction(extraction([
        item('Shirt', '10', service_type='WASH_ONLY'),
        item('Shirt', '7', service_type='IRON_ONLY'),
    ]), trusted_source=True)
    assert all('CONFLICTING_DUPLICATE' not in c.warnings for c in candidates)


def create_job(monkeypatch, rows=None, files=None):
    user = owner()
    laundry = laundry_for(user)
    provider = GeminiScript(fake_gemini_response(extraction(rows or [item('Shirt', '15')]))).install(monkeypatch)
    response = client_for(user).post(reverse('dashboard-price-imports-list'),
                                     {'source_images': files or [upload()], 'third_party_processing_consent': 'true'}, format='multipart')
    assert response.status_code == 201, response.data
    return user, laundry, PriceListImportJob.objects.get(pk=response.data['id']), provider


@pytest.mark.django_db
def test_multiple_images_preserve_page_evidence_and_private_sources(monkeypatch):
    user, _, job, provider = create_job(monkeypatch, files=[upload(seed=1), upload(seed=2)])
    assert len(provider.calls) == 2
    assert job.source_pages == 2
    assert not job.source_image
    assert set(job.draft_items.values_list('pricing_details__source_page', flat=True)) == {1, 2}
    assert not LaundryPricingItem.objects.exists()
    url = reverse('dashboard-price-imports-source', kwargs={'pk': job.pk})
    response = client_for(user).get(url, {'page': 2})
    assert response.status_code == 200
    assert response.content.startswith(b'\xff\xd8')
    assert response['Cache-Control'] == 'private, no-store'
    foreign = owner(email='other@example.com', phone='233500070002')
    assert client_for(foreign).get(url).status_code == 404
    assert client_for(user).get(url, {'page': 3}).status_code == 404


@pytest.mark.django_db
def test_review_can_resume_without_publishing(monkeypatch):
    user, _, job, _ = create_job(monkeypatch)
    url = reverse('dashboard-price-imports-draft', kwargs={'pk': job.pk})
    payload = {'items': [{'draft_id': str(job.draft_items.first().id), 'action': 'CREATE', 'item_name': 'Reviewed shirt', 'category': '', 'unit_price': '17.00', 'pricing_method': 'PER_ITEM'}], 'currency_confirmed': True}
    response = client_for(user).patch(url, payload, format='json')
    assert response.status_code == 200, response.data
    job.refresh_from_db()
    assert job.review_draft == payload
    assert job.draft_items.first().item_name == 'Shirt'
    assert not LaundryPricingItem.objects.exists()
    assert client_for(user).get(reverse('dashboard-price-imports-detail', kwargs={'pk': job.pk})).data['review_draft'] == payload
    foreign = owner(email='other@example.com', phone='233500070002')
    assert client_for(foreign).patch(url, payload, format='json').status_code == 404


@pytest.mark.django_db
def test_reviewed_basket_creation_is_atomic_and_idempotent(monkeypatch):
    user, laundry, job, _ = create_job(monkeypatch, [item('Medium basket', '70', method='PER_BASKET', basket_name='Medium')])
    row = {'draft_id': str(job.draft_items.first().id), 'action': 'CREATE', 'item_name': 'Medium wash and iron',
           'pricing_method': 'PER_BASKET', 'service_type': 'WASH_AND_IRON', 'unit_price': '70.00'}
    url = reverse('dashboard-price-imports-confirm', kwargs={'pk': job.pk})
    assert client_for(user).post(url, {'items': [row]}, format='json').status_code == 400
    assert not BasketDefinition.objects.exists()
    row['basket_definition'] = {'name': 'Medium', 'capacity_description': 'Up to 20 everyday garments',
                                'max_pieces': 20, 'overfill_policy': 'Extra laundry requires approval of a revised quote.'}
    response = client_for(user).post(url, {'items': [row]}, format='json')
    assert response.status_code == 200, response.data
    assert LaundryPricingItem.objects.get(laundry=laundry).unit_price == Decimal('70')
    assert client_for(user).post(url, {'items': [row]}, format='json').status_code == 200
    assert BasketDefinition.objects.count() == 1
    assert LaundryPricingItem.objects.count() == 1


@pytest.mark.django_db
def test_cross_tenant_basket_id_cannot_publish(monkeypatch):
    user, _, job, _ = create_job(monkeypatch)
    foreign = owner(email='other@example.com', phone='233500070002')
    basket = BasketDefinition.objects.create(laundry=laundry_for(foreign, phone='0240000071'), name='Other basket', capacity_description='One bag', overfill_policy='Ask first')
    row = {'action': 'CREATE', 'item_name': 'Basket', 'pricing_basis': 'PER_BASKET', 'basket': str(basket.pk), 'unit_price': '10.00'}
    response = client_for(user).post(reverse('dashboard-price-imports-confirm', kwargs={'pk': job.pk}), {'items': [row]}, format='json')
    assert response.status_code == 400
    assert not LaundryPricingItem.objects.exists()


@pytest.mark.django_db
def test_private_sources_follow_retention(monkeypatch):
    user, _, job, _ = create_job(monkeypatch)
    name, storage = job.source_document.name, job.source_document.storage
    assert storage.exists(name)
    PriceListImportJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=40))
    call_command('purge_price_import_images', days=30, stdout=io.StringIO())
    job.refresh_from_db()
    assert not storage.exists(name)
    assert not job.source_document
    assert job.source_pages == 0
    assert job.draft_items.exists()
