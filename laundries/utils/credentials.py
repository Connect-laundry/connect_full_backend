"""Secure dynamic credentials loader for Google Cloud Vision service.

Security Model:
* Google Cloud service account JSON content is loaded only from the environment variable
  `GOOGLE_APPLICATION_CREDENTIALS_JSON` (Render environment variable).
* The content is validated as JSON before writing.
* A secure temporary file is written dynamically on startup (in memory/temporary directory)
  with owner-only read-write permissions (`0o600`).
* The path to this temporary file is set as the standard `GOOGLE_APPLICATION_CREDENTIALS`
  environment variable for the Google Cloud client libraries.
* The temporary file is automatically deleted when the process exits using `atexit`.
* The raw secret content is NEVER logged, committed, or exposed.
"""

import atexit
import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# Track the temp file path globally to prevent double creation in the same process
_temp_credentials_path = None


def initialize_google_credentials():
    """Dynamically set up Google Cloud credentials from env variable.

    Validates and writes the credentials JSON string to a temporary secure file,
    updating GOOGLE_APPLICATION_CREDENTIALS to point to it. Registers exit hooks
    for clean-up.
    """
    global _temp_credentials_path

    # If already set up in this process, reuse it
    if _temp_credentials_path is not None:
        return _temp_credentials_path

    creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if creds_json:
        creds_json = creds_json.strip()
        # Strip literal wrapping quotes if they exist (common in env vars)
        if (creds_json.startswith('"') and creds_json.endswith('"')) or \
           (creds_json.startswith("'") and creds_json.endswith("'")):
            creds_json = creds_json[1:-1].strip()

    if not creds_json:
        # Check if Google's standard variable is already pointing to a valid file
        existing_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if existing_path and os.path.isfile(existing_path):
            logger.info("GCP env path is already set to an existing file: %s", existing_path)
            return existing_path

        logger.info(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON is not set in environment. "
            "Google Vision OCR service will fall back or error out if invoked."
        )
        return None

    try:
        # Validate that the string is valid JSON
        cleaned_json = creds_json

        # 1. Try decoding as base64 if it doesn't start with JSON brace (common for cloud env variables)
        import base64
        if cleaned_json and not cleaned_json.startswith('{'):
            try:
                decoded = base64.b64decode(cleaned_json).decode('utf-8')
                if decoded.strip().startswith('{'):
                    cleaned_json = decoded.strip()
            except Exception:
                pass

        # 2. Handle common escaping issues (e.g. backslash followed by literal newline)
        # This happens often in env vars where \n gets unescaped to \ + literal newline
        if '\\\n' in cleaned_json:
            cleaned_json = cleaned_json.replace('\\\n', '\\n')
        parsed_creds = json.loads(cleaned_json)

        # Check for service account fields
        if not isinstance(parsed_creds, dict) or parsed_creds.get('type') != 'service_account':
            raise ValueError("Credential JSON must be a service_account type.")

    except Exception as e:
        logger.error(
            "Failed to parse or validate GOOGLE_APPLICATION_CREDENTIALS_JSON. "
            "Ensure the environment variable contains a valid Google service account JSON string. "
            "Error details: %s",
            str(e)
        )
        return None

    try:
        # Create a secure temporary file. By default on Unix, NamedTemporaryFile creates files with 0o600.
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tf:
            json.dump(parsed_creds, tf)
            _temp_credentials_path = tf.name

        # Enforce owner-only read/write permissions (0o600)
        try:
            os.chmod(_temp_credentials_path, 0o600)
        except Exception:
            # os.chmod has limited functionality on Windows but shouldn't crash
            pass

        # Set the environment variable for Google SDK to automatically discover
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = _temp_credentials_path

        # Register cleanup hook on process exit
        def cleanup_temp_credentials():
            global _temp_credentials_path
            if _temp_credentials_path and os.path.exists(_temp_credentials_path):
                try:
                    os.remove(_temp_credentials_path)
                except Exception as ex:
                    logger.debug("Failed to remove temporary GCP auth file: %s", ex)
                _temp_credentials_path = None

        atexit.register(cleanup_temp_credentials)
        logger.info("Successfully initialized GOOGLE_APPLICATION_CREDENTIALS from environment JSON.")
        return _temp_credentials_path

    except Exception as e:
        # Avoid logging credentials or full trace to keep secrets safe
        logger.error("Error writing temporary GCP auth file: %s", str(e))
        return None
