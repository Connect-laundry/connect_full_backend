"""Tests for the GOOGLE_APPLICATION_CREDENTIALS_JSON bootstrap loader."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from laundries.utils.credentials import initialize_google_credentials


# =====================================================================
# CREDENTIAL LOAD TESTS
# =====================================================================

def test_credentials_initialize_empty_env():
    """If env var is empty, whitespace-only, or contains literal empty quotes, initializer returns None or existing path."""
    with patch.dict(os.environ, {}, clear=True):
        path = initialize_google_credentials()
        assert path is None or path == os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

    with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS_JSON': '   '}, clear=True):
        with patch('laundries.utils.credentials._temp_credentials_path', None):
            path = initialize_google_credentials()
            assert path is None

    with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS_JSON': '""'}, clear=True):
        with patch('laundries.utils.credentials._temp_credentials_path', None):
            path = initialize_google_credentials()
            assert path is None

    with patch.dict(os.environ, {'GOOGLE_APPLICATION_CREDENTIALS_JSON': "''"}, clear=True):
        with patch('laundries.utils.credentials._temp_credentials_path', None):
            path = initialize_google_credentials()
            assert path is None

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
