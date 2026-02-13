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
        self.secret_key = os.getenv('PAYSTACK_SECRET_KEY')
        self.base_url = 'https://api.paystack.co'
        self.headers = {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json',
        }

    def initialize_transaction(self, email, amount, reference):
        """
        Initialize a payment transaction.
        Amount should be in kobo (NGN * 100).
        """
        endpoint = f"{self.base_url}/transaction/initialize"
        payload = {
            'email': email,
            'amount': int(float(amount) * 100),
            'reference': reference,
            'callback_url': os.getenv('PAYSTACK_CALLBACK_URL'),
        }
        
        try:
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=10)
            return response.json()
        except Exception as e:
            logger.error(f"Paystack initialization error: {e}")
            return None

    def verify_transaction(self, reference):
        """
        Verify a completed transaction.
        """
        endpoint = f"{self.base_url}/transaction/verify/{reference}"
        
        try:
            response = requests.get(endpoint, headers=self.headers, timeout=10)
            return response.json()
        except Exception as e:
            logger.error(f"Paystack verification error: {e}")
            return None
