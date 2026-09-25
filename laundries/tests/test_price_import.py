"""API / workflow tests for AI-assisted price-list import.

Providers are mocked at their network boundary (``GeminiPriceListProvider._call``
and ``requests.post`` in the OCR.space provider), so the real provider code,
error classification, fallback policy, validation and confirm workflow all run.
No paid API is called.
"""
import logging

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework import status

from laundries.models.laundry import OwnerAuditLog
from laundries.models.price_import import PriceListDraftItem, PriceListImportJob
from laundries.models.pricing import LaundryPricingItem, LaundryWeightPricing, PricingCatalogVersion
from laundries.services.price_import import circuit

from .price_import_helpers import (
    AI_SETTINGS, FakeHTTPResponse, GeminiScript, OCRScript, client_for, customer, extraction,
    fake_gemini_response, genai_error, item, laundry_for, ocr_ok, owner, upload,
)

LIST_URL = 'dashboard-price-imports-list'
DETAIL_URL = 'dashboard-price-imports-detail'
AVAIL_URL = 'dashboard-price-imports-availability'


@pytest.fixture
def ai_settings(settings):
    for key, value in AI_SETTINGS.items():
        setattr(settings, key, value)
    return settings


@pytest.fixture
def shadow_on(settings):
    settings.PRICE_LIST_SHADOW_CROSSCHECK = True


def _confirm_url(pk):
    return reverse('dashboard-price-imports-confirm', kwargs={'pk': pk})


def _cancel_url(pk):
    return reverse('dashboard-price-imports-cancel', kwargs={'pk': pk})


def _good_extraction():
    return extraction([
        item('Shirt', '15', category='Tops'),
        item('Trouser', '20', category='Tops'),
        item('Wash & Fold', '18', method='PER_KG', source='Wash & Fold 18/kg'),
    ])


def _post(client, **kw):
    return client.post(reverse(LIST_URL), {'source_image': upload(**kw)}, format='multipart')


def _ready_job(monkeypatch, user=None, ext=None, seed=0):
    user = user or owner()
    laundry = laundry_for(user)
    GeminiScript(fake_gemini_response(ext or _good_extraction())).install(monkeypatch)
    resp = _post(client_for(user), seed=seed)
    assert resp.status_code == status.HTTP_201_CREATED, resp.data
    return user, laundry, PriceListImportJob.objects.get(id=resp.data['id'])


# --- access control ----------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.usefixtures('ai_settings')
class TestAccess:
    def test_unauthenticated_denied(self, api_client):
        assert api_client.post(reverse(LIST_URL), {}, format='multipart').status_code == 401

    def test_customer_forbidden(self):
        assert _post(client_for(customer())).status_code == 403

    def test_onboarding_owner_without_laundry_can_scan(self, monkeypatch):
        # New owners scan during onboarding, before the laundry exists.
        user = owner()
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.status_code == 201 and resp.data['status'] == 'READY'
        job = PriceListImportJob.objects.get()
        assert job.laundry_id is None and job.created_by == user
        assert LaundryPricingItem.objects.count() == 0
        assert client_for(user).get(reverse(DETAIL_URL, kwargs={'pk': job.id})).status_code == 200

    def test_onboarding_scan_is_private_to_its_owner(self, monkeypatch):
        user = owner()
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        job_id = _post(client_for(user)).data['id']
        other = client_for(owner('peer@example.com', '233500070006'))
        assert other.get(reverse(DETAIL_URL, kwargs={'pk': job_id})).status_code == 404
        assert other.post(_confirm_url(job_id), {'items': [{'item_name': 'X', 'unit_price': '1'}]},
                          format='json').status_code == 404
        assert other.post(_cancel_url(job_id)).status_code == 404

    def test_confirming_onboarding_scan_needs_a_laundry_then_attaches(self, monkeypatch):
        user = owner()
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        job_id = _post(client_for(user)).data['id']
        rows = {'items': [{'item_name': 'Shirt', 'unit_price': '15.00'}]}
        resp = client_for(user).post(_confirm_url(job_id), rows, format='json')
        assert resp.status_code == 409 and resp.data['code'] == 'NO_LAUNDRY'
        laundry = laundry_for(user)
        assert client_for(user).post(_confirm_url(job_id), rows, format='json').status_code == 200
        assert PriceListImportJob.objects.get().laundry_id == laundry.id
        assert LaundryPricingItem.objects.filter(laundry=laundry, item_name='Shirt').exists()

    def test_onboarding_daily_limit_counts_per_owner(self, monkeypatch, settings):
        settings.PRICE_LIST_DAILY_LIMIT_PER_LAUNDRY = 1
        user = owner()
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        assert _post(client_for(user), seed=1).status_code == 201
        assert _post(client_for(user), seed=2).status_code == 429
        # A different onboarding owner has their own allowance.
        assert _post(client_for(owner('o2@example.com', '233500070007')), seed=3).status_code == 201

    def test_async_upload_returns_202_and_job_is_pollable(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        resp = client_for(user).post(reverse(LIST_URL) + '?async=1', {'source_image': upload()}, format='multipart')
        assert resp.status_code == 202
        polled = client_for(user).get(reverse(DETAIL_URL, kwargs={'pk': resp.data['id']}))
        assert polled.data['status'] == 'READY' and len(polled.data['draft_items']) == 3

    def test_other_owner_cannot_read_confirm_or_cancel(self, monkeypatch):
        _, _, job = _ready_job(monkeypatch)
        intruder = owner('intruder@example.com', '233500070002')
        laundry_for(intruder, name='Other', phone='0240000071')
        c = client_for(intruder)
        assert c.get(reverse(DETAIL_URL, kwargs={'pk': job.id})).status_code == 404
        resp = c.post(_confirm_url(job.id), {'items': [{'item_name': 'X', 'unit_price': '1'}]}, format='json')
        assert resp.status_code == 404
        assert c.post(_cancel_url(job.id)).status_code == 404
        assert LaundryPricingItem.objects.count() == 0


# --- feature flag / rollout -------------------------------------------------------

@pytest.mark.django_db
class TestAvailability:
    def test_disabled_by_default_and_manual_pricing_still_works(self):
        user = owner()
        laundry_for(user)
        c = client_for(user)
        resp = _post(c)
        assert resp.status_code == 403 and resp.data['code'] == 'AI_IMPORT_NOT_AVAILABLE'
        assert 'manually' in resp.data['message']
        avail = c.get(reverse(AVAIL_URL)).data['data']
        assert avail['available'] is False and avail['manual_entry_available'] is True
        manual = c.post(reverse('dashboard-pricing-items-list'),
                        {'item_name': 'Shirt', 'unit_price': '12.00'}, format='json')
        assert manual.status_code == 201

    @override_settings(**{**AI_SETTINGS, 'PRICE_LIST_AI_LAUNDRY_ALLOWLIST': ['00000000-0000-0000-0000-000000000000']})
    def test_allowlist_excludes_other_laundries(self):
        user = owner()
        laundry_for(user)
        resp = _post(client_for(user))
        assert resp.status_code == 403

    def test_allowlist_accepts_owner_email_for_onboarding_canary(self, monkeypatch):
        user = owner('qa-owner@simame.test', '233500070008')
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        with override_settings(**{**AI_SETTINGS, 'PRICE_LIST_AI_LAUNDRY_ALLOWLIST': ['qa-owner@simame.test']}):
            assert client_for(user).get(reverse(AVAIL_URL)).data['data']['available'] is True
            assert _post(client_for(user)).status_code == 201
            other = owner()
            assert _post(client_for(other)).status_code == 403

    def test_allowlist_includes_qa_laundry(self, monkeypatch):
        user = owner()
        laundry = laundry_for(user)
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        with override_settings(**{**AI_SETTINGS, 'PRICE_LIST_AI_LAUNDRY_ALLOWLIST': [str(laundry.id)]}):
            assert _post(client_for(user)).status_code == 201

    @override_settings(**{**AI_SETTINGS, 'GEMINI_API_KEY': '', 'OCR_SPACE_API_KEY': ''})
    def test_no_keys_means_not_available(self):
        user = owner()
        laundry_for(user)
        data = client_for(user).get(reverse(AVAIL_URL)).data['data']
        assert data['available'] is False and data['reason'] == 'NOT_CONFIGURED'


# --- extraction happy path + fallback policy ------------------------------------------

@pytest.mark.django_db
@pytest.mark.usefixtures('ai_settings')
class TestExtraction:
    def test_gemini_success_creates_reviewable_drafts_only(self, monkeypatch):
        user, laundry, job = _ready_job(monkeypatch)
        assert job.status == 'READY' and job.provider == 'gemini'
        assert job.model_name == 'gemini-3.8-flash' and job.currency == 'GHS'
        drafts = {d.item_name: d for d in job.draft_items.all()}
        assert drafts['Shirt'].suggested_price == 15 and drafts['Shirt'].review_state == 'LOOKS_GOOD'
        assert drafts['Wash & Fold'].pricing_method == 'PER_KG' and drafts['Wash & Fold'].price_per_kg == 18
        # Nothing is live until the owner confirms.
        assert LaundryPricingItem.objects.count() == 0
        assert not LaundryWeightPricing.objects.exists()
        assert job.image_sha256 and job.provider_trace['attempts'][0]['ok'] is True

    def test_response_shape_is_backward_compatible(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        data = _post(client_for(user)).data
        for key in ('id', 'status', 'provider', 'error', 'draft_items', 'created_at', 'confirmed_at'):
            assert key in data
        row = data['draft_items'][0]
        for key in ('id', 'item_name', 'suggested_price', 'category', 'confidence', 'is_selected',
                    'pricing_method', 'price_per_kg', 'review_state', 'warnings', 'match_type', 'source_text'):
            assert key in row
        assert data['review_required'] is True and data['summary']['total'] == 3
        assert 'provider_trace' not in data and 'result' not in data

    def test_overloaded_primary_model_hops_to_fallback_model(self, monkeypatch):
        user = owner()
        laundry_for(user)
        script = GeminiScript(genai_error(503, 'UNAVAILABLE'), fake_gemini_response(_good_extraction()))
        script.install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.data['status'] == 'READY'
        assert script.calls == ['gemini-3.8-flash', 'gemini-3.6-flash']
        assert PriceListImportJob.objects.get().model_name == 'gemini-3.6-flash'

    def test_gemini_down_falls_back_to_ocr_with_every_row_marked_for_review(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(genai_error(503, 'UNAVAILABLE')).install(monkeypatch)
        ocr = OCRScript(ocr_ok('PRICE LIST\nShirt ........ GH� 15\nWash & Fold 18/kg')).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.status_code == 201 and resp.data['status'] == 'READY'
        assert resp.data['provider'] == 'ocr_space'
        assert all(r['review_state'] != 'LOOKS_GOOD' for r in resp.data['draft_items'])
        assert 'FALLBACK_TEXT_PARSER_USED' in resp.data['document_warnings']
        sent = ocr.calls[0]
        assert sent['data']['OCREngine'] == '3' and sent['data']['isTable'] == 'true'
        assert sent['size'] <= 950 * 1024

    @pytest.mark.parametrize('error', [
        genai_error(429, 'RESOURCE_EXHAUSTED'), genai_error(401, 'UNAUTHENTICATED'),
        genai_error(500, 'INTERNAL'), genai_error(400, 'INVALID_ARGUMENT'),
    ])
    def test_every_gemini_error_class_falls_back(self, monkeypatch, error):
        user = owner()
        laundry_for(user)
        GeminiScript(error).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        assert _post(client_for(user)).data['provider'] == 'ocr_space'

    def test_quota_and_auth_errors_are_not_retried_on_other_model(self, monkeypatch):
        user = owner()
        laundry_for(user)
        script = GeminiScript(genai_error(429, 'RESOURCE_EXHAUSTED')).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        _post(client_for(user))
        assert script.calls == ['gemini-3.8-flash']

    def test_daily_quota_hops_model_then_opens_circuit_long(self, monkeypatch):
        import time
        user = owner()
        laundry_for(user)
        daily = genai_error(429, 'RESOURCE_EXHAUSTED', 'GenerateRequestsPerDayPerProjectPerModel-FreeTier')
        script = GeminiScript(daily).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        assert _post(client_for(user), seed=1).data['provider'] == 'ocr_space'
        assert script.calls == ['gemini-3.8-flash', 'gemini-3.6-flash']   # each model has its own daily quota
        state = circuit._cache().get(circuit._key('gemini'))
        assert state['open_until'] - time.time() > 1000                    # ~30 min, not 2 min
        script.calls.clear()
        _post(client_for(user), seed=2)
        assert script.calls == []                                           # no wasted calls while exhausted

    def test_daily_quota_on_primary_only_uses_fallback_model(self, monkeypatch):
        user = owner()
        laundry_for(user)
        daily = genai_error(429, 'RESOURCE_EXHAUSTED', 'GenerateRequestsPerDayPerProjectPerModel-FreeTier')
        GeminiScript(daily, fake_gemini_response(_good_extraction())).install(monkeypatch)
        data = _post(client_for(user)).data
        assert data['provider'] == 'gemini' and PriceListImportJob.objects.get().model_name == 'gemini-3.6-flash'

    def test_timeout_falls_back(self, monkeypatch):
        import httpx
        user = owner()
        laundry_for(user)
        GeminiScript(httpx.ReadTimeout('slow')).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.data['provider'] == 'ocr_space'
        trace = PriceListImportJob.objects.get().provider_trace
        assert trace['attempts'][0]['kind'] == 'TIMEOUT'

    def test_invalid_structured_output_falls_back(self, monkeypatch):
        from types import SimpleNamespace
        user = owner()
        laundry_for(user)
        GeminiScript(SimpleNamespace(parsed=None, text='{"items": "not a list"}', candidates=[],
                                     usage_metadata=None)).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.data['provider'] == 'ocr_space'
        assert PriceListImportJob.objects.get().provider_trace['attempts'][0]['kind'] == 'SCHEMA'

    def test_both_providers_down_fails_softly(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(genai_error(503, 'UNAVAILABLE')).install(monkeypatch)
        OCRScript(FakeHTTPResponse(503, text='Service Unavailable')).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.status_code == 201 and resp.data['status'] == 'FAILED'
        assert resp.data['error_code'] == 'AI_TEMPORARILY_UNAVAILABLE'
        assert 'manually' in resp.data['error']
        # Owner-facing text never leaks provider internals.
        for word in ('503', 'gemini', 'ocr', 'UNAVAILABLE', 'RESOURCE_EXHAUSTED'):
            assert word.lower() not in resp.data['error'].lower()
        # Manual entry still works during a total AI outage.
        manual = client_for(user).post(reverse('dashboard-pricing-items-list'),
                                       {'item_name': 'Shirt', 'unit_price': '12.00'}, format='json')
        assert manual.status_code == 201

    def test_ocr_engine3_timeout_hops_to_engine2(self, monkeypatch):
        import requests
        user = owner()
        laundry_for(user)
        GeminiScript(genai_error(503, 'UNAVAILABLE')).install(monkeypatch)
        ocr = OCRScript(requests.Timeout('E3 slow'), ocr_ok('Shirt .... GH� 15')).install(monkeypatch)
        data = _post(client_for(user)).data
        assert data['status'] == 'READY' and data['provider'] == 'ocr_space'
        assert [c['data']['OCREngine'] for c in ocr.calls] == ['3', '2']
        assert PriceListImportJob.objects.get().model_name == 'engine2'

    def test_ocr_engine3_server_timeout_e563_hops_to_engine2(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(genai_error(503, 'UNAVAILABLE')).install(monkeypatch)
        e563 = FakeHTTPResponse(504, {'error': 'E563: OCR Engine 3 timed out after 1 minute.'})
        ocr = OCRScript(e563, ocr_ok('Shirt .... 15')).install(monkeypatch)
        assert _post(client_for(user)).data['status'] == 'READY'
        assert [c['data']['OCREngine'] for c in ocr.calls] == ['3', '2']

    def test_ocr_bad_key_does_not_hop(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(genai_error(503, 'UNAVAILABLE')).install(monkeypatch)
        ocr = OCRScript(FakeHTTPResponse(403, text='Invalid API key')).install(monkeypatch)
        assert _post(client_for(user)).data['status'] == 'FAILED'
        assert len(ocr.calls) == 1

    def test_ocr_quota_exhausted_message_classified(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(genai_error(503, 'UNAVAILABLE')).install(monkeypatch)
        OCRScript(FakeHTTPResponse(403, text='You may only perform this action upto maximum 500 number of times within 86400 seconds')).install(monkeypatch)
        _post(client_for(user))
        attempts = PriceListImportJob.objects.get().provider_trace['attempts']
        assert attempts[1]['kind'] == 'QUOTA'

    def test_no_items_found(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(extraction([], currency=None, warnings=['NOT_A_PRICE_LIST']))).install(monkeypatch)
        OCRScript(ocr_ok('')).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.data['status'] == 'FAILED' and resp.data['error_code'] == 'NO_PRICES_FOUND'

    def test_open_circuit_skips_gemini_entirely(self, monkeypatch):
        user = owner()
        laundry_for(user)
        circuit.record_failure('gemini', 'AUTH')
        script = GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        resp = _post(client_for(user))
        assert script.calls == [] and resp.data['provider'] == 'ocr_space'
        assert PriceListImportJob.objects.get().provider_trace['skipped']['gemini'] == 'CIRCUIT_OPEN'

    @override_settings(**{**AI_SETTINGS, 'PRICE_LIST_GEMINI_ENABLED': False})
    def test_gemini_can_be_disabled_independently(self, monkeypatch):
        user = owner()
        laundry_for(user)
        script = GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        assert _post(client_for(user)).data['provider'] == 'ocr_space' and script.calls == []

    @override_settings(**{**AI_SETTINGS, 'PRICE_LIST_OCR_ENABLED': False})
    def test_ocr_can_be_disabled_independently(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(genai_error(503, 'UNAVAILABLE')).install(monkeypatch)
        ocr = OCRScript(ocr_ok('Shirt .... 15')).install(monkeypatch)
        assert _post(client_for(user)).data['status'] == 'FAILED' and ocr.calls == []


# --- shadow cross-check -------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.usefixtures('ai_settings', 'shadow_on')
class TestCrossCheck:
    def test_disagreement_forces_review(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(extraction([item('Shirt', '15')]))).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... GH� 75')).install(monkeypatch)
        row = _post(client_for(user)).data['draft_items'][0]
        assert row['review_state'] == 'CHECK' and 'PRICE_PROVIDER_DISAGREEMENT' in row['warnings']
        assert row['suggested_price'] == '15.00'        # Gemini value kept, not replaced

    def test_agreement_keeps_looks_good(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(extraction([item('Shirt', '15')]))).install(monkeypatch)
        OCRScript(ocr_ok('Shirt .... GH� 15')).install(monkeypatch)
        row = _post(client_for(user)).data['draft_items'][0]
        assert row['review_state'] == 'LOOKS_GOOD'
        assert PriceListImportJob.objects.get().provider_trace['crosscheck']['agreed'] == 1

    def test_ocr_failure_during_crosscheck_keeps_gemini_result(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(extraction([item('Shirt', '15')]))).install(monkeypatch)
        OCRScript(FakeHTTPResponse(500, text='err')).install(monkeypatch)
        data = _post(client_for(user)).data
        assert data['status'] == 'READY' and data['provider'] == 'gemini'

    @override_settings(**{**AI_SETTINGS, 'PRICE_LIST_SHADOW_CROSSCHECK': True, 'PRICE_LIST_SHADOW_CROSSCHECK_LIMIT': 0})
    def test_shadow_ends_after_limit_but_suspicious_rows_still_crosschecked(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(extraction([item('Shirt', '15')]))).install(monkeypatch)
        ocr = OCRScript(ocr_ok('Shirt 15')).install(monkeypatch)
        _post(client_for(user), seed=1)
        assert ocr.calls == []
        GeminiScript(fake_gemini_response(extraction([item('Shirt', '15', source='Shirt 75')]))).install(monkeypatch)
        _post(client_for(user), seed=2)
        assert len(ocr.calls) == 1


# --- dedup / limits / concurrency -----------------------------------------------------

@pytest.mark.django_db
@pytest.mark.usefixtures('ai_settings')
class TestLimits:
    def test_identical_image_while_ready_returns_same_job_without_provider_call(self, monkeypatch):
        user, _, job = _ready_job(monkeypatch)
        script = GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.status_code == 200 and resp.data['id'] == str(job.id) and resp.data['deduplicated']
        assert script.calls == []

    def test_identical_image_after_confirm_reuses_result_without_provider_call(self, monkeypatch):
        user, _, job = _ready_job(monkeypatch)
        client_for(user).post(_cancel_url(job.id))
        script = GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        resp = _post(client_for(user))
        assert resp.status_code == 201 and resp.data['id'] != str(job.id)
        assert resp.data['served_from_cache'] is True and script.calls == []
        assert len(resp.data['draft_items']) == 3

    def test_cache_never_shared_across_laundries(self, monkeypatch):
        _ready_job(monkeypatch)
        other = owner('other@example.com', '233500070003')
        laundry_for(other, name='Other', phone='0240000072')
        script = GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        resp = _post(client_for(other))
        assert resp.status_code == 201 and resp.data['served_from_cache'] is False
        assert script.calls == ['gemini-3.8-flash']

    @override_settings(**{**AI_SETTINGS, 'PRICE_LIST_DAILY_LIMIT_PER_LAUNDRY': 2})
    def test_daily_limit_per_laundry(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(_good_extraction())).install(monkeypatch)
        c = client_for(user)
        assert _post(c, seed=1).status_code == 201
        assert _post(c, seed=2).status_code == 201
        resp = _post(c, seed=3)
        assert resp.status_code == 429 and resp.data['code'] == 'DAILY_LIMIT_REACHED'

    def test_in_progress_import_blocks_second_upload(self, monkeypatch):
        user = owner()
        laundry = laundry_for(user)
        PriceListImportJob.objects.create(laundry=laundry, status='PROCESSING', image_sha256='x' * 64)
        resp = _post(client_for(user))
        assert resp.status_code == 409 and resp.data['code'] == 'IMPORT_IN_PROGRESS'

    def test_global_concurrency_cap(self, monkeypatch):
        other = owner('busy@example.com', '233500070004')
        PriceListImportJob.objects.create(laundry=laundry_for(other, name='Busy', phone='0240000073'),
                                          status='PROCESSING')
        user = owner()
        laundry_for(user)
        resp = _post(client_for(user))
        assert resp.status_code == 503 and resp.data['code'] == 'SERVER_BUSY'

    def test_stale_processing_job_expires(self, monkeypatch):
        from datetime import timedelta
        from django.utils import timezone
        user = owner()
        laundry = laundry_for(user)
        stale = PriceListImportJob.objects.create(laundry=laundry, status='PROCESSING')
        PriceListImportJob.objects.filter(id=stale.id).update(updated_at=timezone.now() - timedelta(minutes=10))
        resp = client_for(user).get(reverse(DETAIL_URL, kwargs={'pk': stale.id}))
        assert resp.data['status'] == 'FAILED'

    def test_upload_rejections_surface_codes(self):
        user = owner()
        laundry_for(user)
        c = client_for(user)
        from django.core.files.uploadedfile import SimpleUploadedFile
        evil = SimpleUploadedFile('menu.jpg', b'MZ' + b'\x00' * 300, content_type='image/jpeg')
        resp = c.post(reverse(LIST_URL), {'source_image': evil}, format='multipart')
        assert resp.status_code == 415 and resp.data['code'] == 'UNSUPPORTED_FILE'
        resp = c.post(reverse(LIST_URL), {}, format='multipart')
        assert resp.status_code == 400 and resp.data['code'] == 'NO_FILE'
        assert PriceListImportJob.objects.count() == 0


# --- confirm ------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.usefixtures('ai_settings')
class TestConfirm:
    def _rows(self, job):
        drafts = {d.item_name: d for d in job.draft_items.all()}
        return drafts, [
            {'draft_id': str(drafts['Shirt'].id), 'action': 'CREATE', 'pricing_method': 'PER_ITEM',
             'item_name': 'Shirt', 'unit_price': '16.00', 'category': 'Tops'},
            {'draft_id': str(drafts['Trouser'].id), 'action': 'IGNORE'},
            {'draft_id': str(drafts['Wash & Fold'].id), 'action': 'CREATE', 'pricing_method': 'PER_KG',
             'price_per_kg': '18.00'},
            {'action': 'CREATE', 'pricing_method': 'PER_ITEM', 'item_name': 'Kaftan', 'unit_price': '30'},
        ]

    def test_confirm_applies_owner_edits_atomically_with_audit(self, monkeypatch):
        user, laundry, job = _ready_job(monkeypatch)
        _, rows = self._rows(job)
        resp = client_for(user).post(_confirm_url(job.id), {'items': rows}, format='json')
        assert resp.status_code == 200, resp.data
        items = {i.item_name: i for i in LaundryPricingItem.objects.filter(laundry=laundry)}
        assert set(items) == {'Shirt', 'Kaftan'} and str(items['Shirt'].unit_price) == '16.00'
        assert LaundryWeightPricing.objects.get(laundry=laundry).base_price_per_kg == 18
        job.refresh_from_db()
        assert job.status == 'CONFIRMED' and job.confirm_result['edits']['edited'] == 1
        assert job.confirm_result['edits']['added'] == 1 and job.confirm_result['edits']['ignored'] == 1
        assert OwnerAuditLog.objects.filter(action='PRICE_LIST_AI_IMPORT').count() == 1
        assert PricingCatalogVersion.objects.filter(laundry=laundry).count() == 1

    def test_repeat_confirm_is_idempotent(self, monkeypatch):
        user, laundry, job = _ready_job(monkeypatch)
        _, rows = self._rows(job)
        c = client_for(user)
        first = c.post(_confirm_url(job.id), {'items': rows}, format='json')
        second = c.post(_confirm_url(job.id), {'items': rows}, format='json')
        assert second.status_code == 200 and second.data['data']['already_confirmed'] is True
        assert second.data['data']['created'] == first.data['data']['created']
        assert LaundryPricingItem.objects.filter(laundry=laundry).count() == 2

    def test_invalid_row_rolls_back_everything(self, monkeypatch):
        user, laundry, job = _ready_job(monkeypatch)
        _, rows = self._rows(job)
        rows[-1]['unit_price'] = '-5'
        resp = client_for(user).post(_confirm_url(job.id), {'items': rows}, format='json')
        assert resp.status_code == 400 and resp.data['code'] == 'VALIDATION_FAILED'
        assert resp.data['data']['errors'][0]['row'] == 3
        assert LaundryPricingItem.objects.count() == 0 and not LaundryWeightPricing.objects.exists()
        job.refresh_from_db()
        assert job.status == 'READY'

    @pytest.mark.parametrize('bad', ['abc', '15.001', '1e9', 'NaN', 'Infinity', '100001'])
    def test_server_revalidates_prices(self, monkeypatch, bad):
        user, _, job = _ready_job(monkeypatch)
        rows = [{'action': 'CREATE', 'pricing_method': 'PER_ITEM', 'item_name': 'Shirt', 'unit_price': bad}]
        resp = client_for(user).post(_confirm_url(job.id), {'items': rows}, format='json')
        assert resp.status_code == 400 and LaundryPricingItem.objects.count() == 0

    def test_unknown_pricing_method_must_be_resolved(self, monkeypatch):
        user, _, job = _ready_job(monkeypatch)
        rows = [{'action': 'CREATE', 'pricing_method': 'UNKNOWN', 'item_name': 'Duvet', 'unit_price': '60'}]
        resp = client_for(user).post(_confirm_url(job.id), {'items': rows}, format='json')
        assert resp.status_code == 400

    def test_only_one_per_kg_row(self, monkeypatch):
        user, _, job = _ready_job(monkeypatch)
        rows = [{'action': 'CREATE', 'pricing_method': 'PER_KG', 'price_per_kg': '18'},
                {'action': 'CREATE', 'pricing_method': 'PER_KG', 'price_per_kg': '25'}]
        assert client_for(user).post(_confirm_url(job.id), {'items': rows}, format='json').status_code == 400

    def test_existing_per_kg_needs_explicit_update(self, monkeypatch):
        user, laundry, job = _ready_job(monkeypatch)
        LaundryWeightPricing.objects.create(laundry=laundry, base_price_per_kg='12.00')
        c = client_for(user)
        rows = [{'action': 'CREATE', 'pricing_method': 'PER_KG', 'price_per_kg': '18'}]
        assert c.post(_confirm_url(job.id), {'items': rows}, format='json').status_code == 400
        rows[0]['action'] = 'UPDATE'
        assert c.post(_confirm_url(job.id), {'items': rows}, format='json').status_code == 200
        assert LaundryWeightPricing.objects.get(laundry=laundry).base_price_per_kg == 18

    def test_possible_match_and_update_existing(self, monkeypatch):
        user = owner()
        laundry = laundry_for(user)
        existing = LaundryPricingItem.objects.create(laundry=laundry, item_name='Shirt Wash and Iron', unit_price='15.00')
        GeminiScript(fake_gemini_response(extraction([item('Wash & Iron Shirt', '18')]))).install(monkeypatch)
        data = _post(client_for(user)).data
        row = data['draft_items'][0]
        assert row['match_type'] == 'POSSIBLE' and row['matched_item']['id'] == str(existing.id)
        rows = [{'draft_id': row['id'], 'action': 'UPDATE', 'pricing_method': 'PER_ITEM',
                 'item_name': row['item_name'], 'unit_price': '18.00', 'existing_item_id': str(existing.id)}]
        resp = client_for(user).post(_confirm_url(data['id']), {'items': rows}, format='json')
        assert resp.status_code == 200 and resp.data['data']['updated'] == ['Shirt Wash and Iron']
        existing.refresh_from_db()
        assert str(existing.unit_price) == '18.00'
        assert LaundryPricingItem.objects.filter(laundry=laundry).count() == 1

    def test_update_cannot_target_another_laundrys_item(self, monkeypatch):
        other = owner('victim@example.com', '233500070005')
        victim_item = LaundryPricingItem.objects.create(
            laundry=laundry_for(other, name='Victim', phone='0240000074'), item_name='Shirt', unit_price='10.00')
        user, _, job = _ready_job(monkeypatch)
        rows = [{'action': 'UPDATE', 'pricing_method': 'PER_ITEM', 'item_name': 'Shirt',
                 'unit_price': '1.00', 'existing_item_id': str(victim_item.id)}]
        resp = client_for(user).post(_confirm_url(job.id), {'items': rows}, format='json')
        assert resp.status_code == 400
        victim_item.refresh_from_db()
        assert str(victim_item.unit_price) == '10.00'

    def test_create_never_overwrites_existing(self, monkeypatch):
        user, laundry, job = _ready_job(monkeypatch)
        LaundryPricingItem.objects.create(laundry=laundry, item_name='Shirt', unit_price='9.00')
        resp = client_for(user).post(_confirm_url(job.id), {'items': [
            {'item_name': 'Shirt', 'unit_price': '16.00'}, {'item_name': 'Duvet', 'unit_price': '60'}]}, format='json')
        assert resp.status_code == 200
        assert resp.data['data']['skipped'] == ['Shirt'] and resp.data['data']['created'] == ['Duvet']
        assert str(LaundryPricingItem.objects.get(laundry=laundry, item_name='Shirt').unit_price) == '9.00'

    def test_legacy_payload_still_accepted(self, monkeypatch):
        user, laundry, job = _ready_job(monkeypatch)
        resp = client_for(user).post(_confirm_url(job.id), {'items': [
            {'item_name': 'Shirt', 'unit_price': '12.50', 'category': 'Tops'}]}, format='json')
        assert resp.status_code == 200 and resp.data['data']['created'] == ['Shirt']

    def test_currency_confirmation_required_when_uncertain(self, monkeypatch):
        user = owner()
        laundry_for(user)
        GeminiScript(fake_gemini_response(extraction([item('Shirt', '15', source='Shirt 15')], currency=None))).install(monkeypatch)
        data = _post(client_for(user)).data
        assert 'CURRENCY_UNCONFIRMED' in data['document_warnings']
        rows = [{'action': 'CREATE', 'pricing_method': 'PER_ITEM', 'item_name': 'Shirt', 'unit_price': '15'}]
        c = client_for(user)
        resp = c.post(_confirm_url(data['id']), {'items': rows}, format='json')
        assert resp.status_code == 400 and resp.data['code'] == 'CURRENCY_CONFIRMATION_REQUIRED'
        resp = c.post(_confirm_url(data['id']), {'items': rows, 'currency_confirmed': True}, format='json')
        assert resp.status_code == 200

    def test_duplicate_names_in_request_rejected(self, monkeypatch):
        user, _, job = _ready_job(monkeypatch)
        rows = [{'action': 'CREATE', 'pricing_method': 'PER_ITEM', 'item_name': 'Shirt', 'unit_price': '1'},
                {'action': 'CREATE', 'pricing_method': 'PER_ITEM', 'item_name': ' shirt ', 'unit_price': '2'}]
        assert client_for(user).post(_confirm_url(job.id), {'items': rows}, format='json').status_code == 400

    def test_failed_or_cancelled_job_cannot_be_confirmed(self, monkeypatch):
        user, _, job = _ready_job(monkeypatch)
        c = client_for(user)
        assert c.post(_cancel_url(job.id)).data['status'] == 'CANCELLED'
        resp = c.post(_confirm_url(job.id), {'items': [{'item_name': 'X', 'unit_price': '1'}]}, format='json')
        assert resp.status_code == 409 and LaundryPricingItem.objects.count() == 0

    def test_nothing_selected(self, monkeypatch):
        user, _, job = _ready_job(monkeypatch)
        resp = client_for(user).post(_confirm_url(job.id), {'items': [{'action': 'IGNORE'}]}, format='json')
        assert resp.status_code == 400 and resp.data['code'] == 'NOTHING_TO_IMPORT'


# --- secrets ----------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(**{**AI_SETTINGS, 'GEMINI_API_KEY': 'AQ.SECRET-GEMINI-VALUE', 'OCR_SPACE_API_KEY': 'KSECRETOCR'})
def test_keys_never_appear_in_responses_logs_or_db(monkeypatch, caplog):
    user = owner()
    laundry_for(user)
    GeminiScript(genai_error(401, 'UNAUTHENTICATED')).install(monkeypatch)
    OCRScript(FakeHTTPResponse(403, text='Invalid API key')).install(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        resp = _post(client_for(user))
    blob = str(resp.data) + caplog.text + str(list(PriceListImportJob.objects.values()))
    blob += str(list(PriceListDraftItem.objects.values()))
    assert 'SECRET-GEMINI-VALUE' not in blob and 'KSECRETOCR' not in blob
    assert resp.data['status'] == 'FAILED'


@pytest.mark.django_db
@override_settings(**{**AI_SETTINGS, 'GEMINI_API_KEY': 'the-new-key'})
def test_stray_google_api_key_env_cannot_override(monkeypatch):
    """The SDK prefers GOOGLE_API_KEY from env; we must always send ours."""
    from laundries.services.price_import.providers.gemini import GeminiPriceListProvider
    monkeypatch.setenv('GOOGLE_API_KEY', 'old-standard-key')
    client = GeminiPriceListProvider()._client(10)
    try:
        assert client._api_client.api_key == 'the-new-key'
        assert client._api_client.vertexai is False
    finally:
        client.close()
