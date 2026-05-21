import requests
import os
import logging
from django.conf import settings
from config.redaction import mask_reference, summarize_exception

logger = logging.getLogger(__name__)

class PaystackService:
    """
    Service for Paystack payment integration.
    Handles payment initialization and verification.
    """
    def __init__(self):
        self.secret_key = getattr(settings, 'PAYSTACK_SECRET_KEY', None)
        self.base_url = 'https://api.paystack.co'
        self.headers = {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json',
        }

    def initialize_transaction(self, email, amount, reference, metadata=None):
        """
        Initialize a payment transaction.
        Amount should be in minor currency units (e.g. pesewas for GHS).
        """
        endpoint = f"{self.base_url}/transaction/initialize"
        payload = {
            'email': email,
            'amount': int(float(amount) * 100),
            'currency': 'GHS',
            'reference': reference,
            'metadata': metadata or {}
        }

        callback_url = getattr(settings, 'PAYSTACK_CALLBACK_URL', None)
        if callback_url:
            payload['callback_url'] = callback_url
        
        try:
            response = requests.post(
                endpoint, 
                json=payload, 
                headers=self.headers, 
                timeout=15
            )
            try:
                data = response.json()
            except ValueError:
                logger.error(
                    "Paystack initialization returned non-JSON response",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
                return {'status': False, 'message': 'Payment provider returned an invalid response.'}
            if not response.ok:
                logger.error(
                    "Paystack initialization failed",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
            return data
        except requests.exceptions.RequestException as e:
            logger.error("Paystack request error", extra={"error": summarize_exception(e)})
            return {'status': False, 'message': str(e)}

    def verify_transaction(self, reference):
        """
        Verify a completed transaction.
        """
        endpoint = f"{self.base_url}/transaction/verify/{reference}"
        
        try:
            response = requests.get(
                endpoint, 
                headers=self.headers, 
                timeout=15
            )
            try:
                data = response.json()
            except ValueError:
                logger.error(
                    "Paystack verification returned non-JSON response",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
                return {'status': False, 'message': 'Payment provider returned an invalid response.'}
            if not response.ok:
                logger.error(
                    "Paystack verification failed",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
            return data
        except requests.exceptions.RequestException as e:
            logger.error(
                "Paystack verification error",
                extra={"reference": mask_reference(reference), "error": summarize_exception(e)},
            )
            return {'status': False, 'message': str(e)}
