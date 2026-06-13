"""Google Vision OCR integration provider.

Provides a subclass of BaseOCRProvider that connects to Google Cloud Vision API
and extracts candidate pricing items from images.
"""

import logging
from google.cloud import vision
from .ocr import BaseOCRProvider
from laundries.utils.ocr_parser import parse_ocr_text

logger = logging.getLogger(__name__)


class GoogleVisionOCRProvider(BaseOCRProvider):
    """OCR Provider powered by Google Cloud Vision API."""

    name = 'google_vision'

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazily initialize and return the Vision API client."""
        if self._client is None:
            # Ensure credentials are dynamically loaded from environment json
            from laundries.utils.credentials import initialize_google_credentials
            initialize_google_credentials()
            
            logger.info("Initializing Google Vision ImageAnnotatorClient.")
            self._client = vision.ImageAnnotatorClient()
        return self._client

    def extract(self, image_file) -> list[dict]:
        """Perform OCR on the provided image and return candidate pricing items.

        Args:
            image_file: A Django FieldFile or file-like object containing the image.

        Returns:
            list[dict]: A list of candidate dictionaries containing item_name,
                        suggested_price, category, and confidence.
        """
        logger.info("Google Vision OCR service called for image extraction.")
        
        try:
            image_file.seek(0)
            content = image_file.read()
        except Exception as e:
            logger.error("Failed to read uploaded image contents: %s", str(e))
            raise ValueError("Could not read uploaded image file data.") from e

        if not content:
            logger.warning("Empty content provided for price list OCR.")
            return []

        client = self._get_client()
        image = vision.Image(content=content)

        try:
            # We use document_text_detection because it is optimized for dense text
            # like printed or handwritten spreadsheets and price lists.
            response = client.document_text_detection(image=image)
        except Exception as e:
            # Log technical details internally, raise clean wrapper error
            logger.exception("Google Cloud Vision API call failed: %s", str(e))
            raise RuntimeError(
                "Google Vision OCR service is temporarily unavailable. "
                "Verify API status and service credentials configuration."
            ) from e

        # Handle API error response
        if response.error.message:
            logger.error("Google Cloud Vision returned error: %s", response.error.message)
            raise RuntimeError(f"Google Vision OCR failed: {response.error.message}")

        annotation = response.full_text_annotation
        if not annotation or not annotation.text:
            logger.info("No text detected in the uploaded price list image.")
            return []

        raw_text = annotation.text
        logger.info("Raw text successfully retrieved from Google Vision API. Initializing parse.")
        
        # Parse text into candidate items
        candidates = parse_ocr_text(raw_text)
        return candidates
