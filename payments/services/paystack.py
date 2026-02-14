import requests
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class PaystackService:
    """
    Service for Paystack payment integration.
    Handles payment initialization and verification.
    """
    def __init__(self):
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        self.base_url = 'https://api.paystack.co'
        self.headers = {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json',
        }

    def initialize_transaction(self, email, amount, reference, metadata=None):
        """
        Initialize a payment transaction.
        Amount should be in kobo (NGN * 100).
        """
        endpoint = f"{self.base_url}/transaction/initialize"
        payload = {
            'email': email,
            'amount': int(float(amount) * 100),
            'reference': reference,
            'callback_url': settings.PAYSTACK_CALLBACK_URL,
            'metadata': metadata or {}
        }
        
        try:
            response = requests.post(
                endpoint, 
                json=payload, 
                headers=self.headers, 
                timeout=15
            )
            data = response.json()
            if not response.ok:
                logger.error(f"Paystack initialization failed: {data.get('message', 'Unknown error')}")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Paystack request error: {e}")
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
            data = response.json()
            if not response.ok:
                logger.error(f"Paystack verification failed: {data.get('message', 'Unknown error')}")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Paystack verification error: {e}")
            return {'status': False, 'message': str(e)}
