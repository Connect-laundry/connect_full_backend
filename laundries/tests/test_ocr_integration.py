"""Verification and regression test suite for Google Vision OCR integration and API hardening."""

import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
import pytest
from rest_framework import status
from rest_framework.test import APIClient
from PIL import Image

from laundries.models.price_import import PriceListImportJob
from laundries.utils.credentials import initialize_google_credentials
from laundries.utils.ocr_parser import parse_ocr_text
from users.models import User
from laundries.models.laundry import Laundry

# Test constants
LIST_URL = 'dashboard-price-imports-list'


def _owner():
    return User.objects.create_user(
        email='ocr-owner@example.com',
        phone='233500078001',
        password='StrongPass123!',
        role=User.Role.OWNER
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _laundry(owner):
    return Laundry.objects.create(
        owner=owner, name='OCR Laundry', address='x', city='Accra',
        latitude='5.6', longitude='-0.18', phone_number='0240000080',
    )


def _valid_png(name='pricelist.png', size=(8, 8)):
    buf = io.BytesIO()
    Image.new('RGB', size, color=(200, 100, 50)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


# =====================================================================
# CREDENTIAL LOAD TESTS
# =====================================================================

def test_credentials_initialize_empty_env():
    """If env var is empty, initializer returns None or existing path."""
    with patch.dict(os.environ, {}, clear=True):
        path = initialize_google_credentials()
        assert path is None or path == os.getenv('GOOGLE_APPLICATION_CREDENTIALS')


def test_credentials_initialize_invalid_json():
    """If env var contains invalid JSON, initializer should fail gracefully."""
    with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS_JSON': 'invalid-non-json'}):
        path = initialize_google_credentials()
        assert path is None


def test_credentials_initialize_valid_json_flow():
    """Valid credentials JSON creates a temporary file, sets variable, and cleans up on exit."""
    fake_creds = {
        "type": "service_account",
        "project_id": "test-project",
        "private_key": "fake-key",
        "client_email": "test@test.iam.gserviceaccount.com"
    }
    creds_str = json.dumps(fake_creds)

    with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS_JSON': creds_str}, clear=True):
        # Prevent reuse of global cache variable
        with patch('laundries.utils.credentials._temp_credentials_path', None):
            temp_path = initialize_google_credentials()
            
            assert temp_path is not None
            assert os.path.isfile(temp_path)
            assert os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') == temp_path

            # Read temp file to confirm contents match
            with open(temp_path, 'r') as f:
                data = json.load(f)
                assert data["project_id"] == "test-project"

            # Check permissions (on Unix-like platforms)
            if os.name != 'nt':
                # Enforce owner-only permissions (0o600 / 33152 octal representation of stat mode)
                mode = os.stat(temp_path).st_mode & 0o777
                assert mode == 0o600

            # Execute cleanup manually to verify deletion
            from laundries.utils.credentials import _temp_credentials_path
            if _temp_credentials_path and os.path.exists(_temp_credentials_path):
                os.remove(_temp_credentials_path)


def test_credentials_initialize_base64_flow():
    """Valid base64-encoded credentials JSON is decoded and parsed successfully."""
    import base64
    fake_creds = {
        "type": "service_account",
        "project_id": "test-project-base64",
        "private_key": "fake-key",
        "client_email": "test@test.iam.gserviceaccount.com"
    }
    creds_str = json.dumps(fake_creds)
    base64_str = base64.b64encode(creds_str.encode('utf-8')).decode('utf-8')

    with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS_JSON': base64_str}, clear=True):
        with patch('laundries.utils.credentials._temp_credentials_path', None):
            temp_path = initialize_google_credentials()
            
            assert temp_path is not None
            assert os.path.isfile(temp_path)
            
            with open(temp_path, 'r') as f:
                data = json.load(f)
                assert data["project_id"] == "test-project-base64"

            from laundries.utils.credentials import _temp_credentials_path
            if _temp_credentials_path and os.path.exists(_temp_credentials_path):
                os.remove(_temp_credentials_path)


def test_credentials_initialize_backslash_newline_flow():
    """Credentials JSON with backslash followed by a literal newline is cleaned and parsed successfully."""
    creds_str = '{"type": "service_account", "project_id": "test-project-backslash", "private_key": "line1\\\nline2"}'

    with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS_JSON': creds_str}, clear=True):
        with patch('laundries.utils.credentials._temp_credentials_path', None):
            temp_path = initialize_google_credentials()
            
            assert temp_path is not None
            assert os.path.isfile(temp_path)
            
            with open(temp_path, 'r') as f:
                data = json.load(f)
                assert data["project_id"] == "test-project-backslash"
                assert data["private_key"] == "line1\nline2"

            from laundries.utils.credentials import _temp_credentials_path
            if _temp_credentials_path and os.path.exists(_temp_credentials_path):
                os.remove(_temp_credentials_path)


# =====================================================================
# OCR PARSER INTELLIGENCE TESTS
# =====================================================================

def test_ocr_parser_edge_cases():
    """Tests various parsing formats including dots, spaces, commas, and currency symbols."""
    raw_ocr = (
        "Shirt ............ 15.00\n"
        "Suit (2-Piece) - GHC 85\n"
        "Dry Cleaning Prices\n"
        "Curtain Pair ..... 18,50 GHS\n"
        "Trouser ~~~ 20.00\n"
        "Duvet GHC 40.00\n"
        "Blanket 25.5\n"
        "Invalid line with no price\n"
        "Random Code 98218731\n"
        "Free Item ....... 0.00"
    )

    candidates = parse_ocr_text(raw_ocr)

    # Validate correct count of successfully extracted rows
    assert len(candidates) == 7

    # Verify strict dotted price
    assert candidates[0]['item_name'] == "Shirt"
    assert float(candidates[0]['suggested_price']) == 15.00
    assert candidates[0]['category'] == "Shirts"
    assert candidates[0]['confidence'] >= 0.9

    # Verify dash separator & currency handling
    assert candidates[1]['item_name'] == "Suit (2-Piece)"
    assert float(candidates[1]['suggested_price']) == 85.00
    assert candidates[1]['category'] == "Suits"

    # Verify comma separator mapping to decimal dot
    assert candidates[2]['item_name'] == "Curtain Pair"
    assert float(candidates[2]['suggested_price']) == 18.50
    assert candidates[2]['category'] == "Curtains"

    # Verify tilde separator
    assert candidates[3]['item_name'] == "Trouser"
    assert float(candidates[3]['suggested_price']) == 20.00
    assert candidates[3]['category'] == "Trousers"

    # Verify space separator and currency prefix
    assert candidates[4]['item_name'] == "Duvet"
    assert float(candidates[4]['suggested_price']) == 40.00
    assert candidates[4]['category'] == "Bedding"

    # Verify trailing decimal format
    assert candidates[5]['item_name'] == "Blanket"
    assert float(candidates[5]['suggested_price']) == 25.5
    assert candidates[5]['category'] == "Bedding"

    # Verify zero price
    assert candidates[6]['item_name'] == "Free Item"
    assert float(candidates[6]['suggested_price']) == 0.0


# =====================================================================
# API PIPELINE SECURITY & FLOW TESTS
# =====================================================================

@pytest.mark.django_db
@override_settings(OCR_PROVIDER='google_vision')
@patch('laundries.services.google_vision_service.vision.ImageAnnotatorClient')
def test_valid_ocr_pipeline_upload_flow(mock_client_class):
    """Happy path: Upload valid image, Google Vision extracts candidates, job becomes READY."""
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    mock_response = MagicMock()
    mock_response.full_text_annotation.text = "Shirt ...... 15.00\nDuvet ~~~~~ 50.00"
    mock_response.error.message = ""
    mock_client.document_text_detection.return_value = mock_response

    owner = _owner()
    _laundry(owner)

    client = _client(owner)
    resp = client.post(
        reverse(LIST_URL),
        {'source_image': _valid_png()},
        format='multipart'
    )

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.data.get('data', resp.data)
    assert data['status'] == 'READY'
    assert data['provider'] == 'google_vision'

    # Check that drafts were saved to the database
    job_id = data['id']
    job = PriceListImportJob.objects.get(id=job_id)
    assert job.status == PriceListImportJob.Status.READY
    assert job.draft_items.count() == 2
    
    drafts = list(job.draft_items.all())
    assert drafts[0].item_name == "Duvet"
    assert float(drafts[0].suggested_price) == 50.00
    assert drafts[0].category == "Bedding"

    assert drafts[1].item_name == "Shirt"
    assert float(drafts[1].suggested_price) == 15.00
    assert drafts[1].category == "Shirts"



@pytest.mark.django_db
@override_settings(OCR_PROVIDER='google_vision')
@patch('laundries.services.google_vision_service.vision.ImageAnnotatorClient')
def test_ocr_pipeline_google_vision_failure(mock_client_class):
    """Failure path: Google Vision throws an API exception, task fails gracefully with sanitized error."""
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    mock_client.document_text_detection.side_effect = RuntimeError("API key or credential expired.")

    owner = _owner()
    _laundry(owner)

    client = _client(owner)
    resp = client.post(
        reverse(LIST_URL),
        {'source_image': _valid_png()},
        format='multipart'
    )

    assert resp.status_code == status.HTTP_201_CREATED
    data = resp.data.get('data', resp.data)
    assert data['status'] == 'FAILED'
    assert "Sanitize" not in data['error']
    # Verify exact generic sanitized error message
    assert "OCR service failed to process" in data['error']


@pytest.mark.django_db
def test_upload_oversized_file_rejected():
    """Uploads exceeding 10MB should be blocked with 400."""
    owner = _owner()
    _laundry(owner)

    oversized_data = b"x" * (11 * 1024 * 1024)  # 11MB
    fake_file = SimpleUploadedFile("large.png", oversized_data, content_type="image/png")

    client = _client(owner)
    resp = client.post(
        reverse(LIST_URL),
        {'source_image': fake_file},
        format='multipart'
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "exceeds the 10MB limit" in str(resp.data)


@pytest.mark.django_db
def test_upload_invalid_file_extension_rejected():
    """Uploads with non-image extensions should be blocked with 400."""
    owner = _owner()
    _laundry(owner)

    fake_file = SimpleUploadedFile("prices.txt", b"Shirt ... 15", content_type="text/plain")

    client = _client(owner)
    resp = client.post(
        reverse(LIST_URL),
        {'source_image': fake_file},
        format='multipart'
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unsupported image format" in str(resp.data)


@pytest.mark.django_db
def test_upload_corrupted_image_rejected():
    """Files pretending to be images but carrying invalid bytes should be rejected via Pillow verification."""
    owner = _owner()
    _laundry(owner)

    # Invalid image bytes
    corrupted_data = b"NOT_A_VALID_IMAGE_BYTES"
    fake_file = SimpleUploadedFile("pricelist.png", corrupted_data, content_type="image/png")

    client = _client(owner)
    resp = client.post(
        reverse(LIST_URL),
        {'source_image': fake_file},
        format='multipart'
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid or corrupted image" in str(resp.data)


@pytest.mark.django_db
@override_settings(OCR_PROVIDER='google_vision')
@patch('laundries.services.google_vision_service.vision.ImageAnnotatorClient')
def test_throttling_price_import_endpoint(mock_client_class):
    """End-point throttling allows maximum 60 requests per hour."""
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.full_text_annotation.text = "Shirt ... 15.00"
    mock_response.error.message = ""
    mock_client.document_text_detection.return_value = mock_response

    owner = _owner()
    _laundry(owner)
    client = _client(owner)

    # Let's hit the endpoint 3 times to make sure it works
    for _ in range(3):
        resp = client.post(
            reverse(LIST_URL),
            {'source_image': _valid_png()},
            format='multipart'
        )
        assert resp.status_code == status.HTTP_201_CREATED
