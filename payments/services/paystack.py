import requests
import logging
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from config.redaction import mask_reference, summarize_exception

logger = logging.getLogger(__name__)


def to_minor_units(amount):
    """Convert a major-unit money value to an integer without float rounding."""
    value = Decimal(str(amount)) * Decimal('100')
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


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

    def create_subaccount(self, business_name, settlement_bank, account_number, percentage_charge=0):
        """
        Register a laundry's payout account with Paystack.

        Returns the raw Paystack response; the caller stores
        ``data.subaccount_code``. Bank details are deliberately not persisted
        locally — Paystack is the system of record for them, and holding a
        copy would make this database a target for nothing gained.

        ``percentage_charge`` is Paystack's field for the split ratio. Its
        direction is documented inconsistently by Paystack itself, so this
        integration does not rely on it: the platform's cut is sent per
        transaction as ``transaction_charge``, which overrides it and is
        documented unambiguously as a flat fee taken for the main account.
        """
        endpoint = f"{self.base_url}/subaccount"
        payload = {
            'business_name': business_name,
            'settlement_bank': settlement_bank,
            'account_number': account_number,
            'percentage_charge': percentage_charge,
        }

        try:
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=15)
            try:
                data = response.json()
            except ValueError:
                logger.error(
                    "Paystack subaccount creation returned non-JSON response",
                    extra={"status_code": response.status_code},
                )
                return {'status': False, 'message': 'Payment provider returned an invalid response.'}
            if not response.ok:
                logger.error(
                    "Paystack subaccount creation failed",
                    extra={"status_code": response.status_code, "message": data.get('message')},
                )
            return data
        except requests.exceptions.RequestException as e:
            logger.error("Paystack subaccount request error", extra={"error": summarize_exception(e)})
            return {'status': False, 'message': str(e)}

    def create_transfer_recipient(self, name, account_number, bank_code, recipient_type='ghipss', currency='GHS'):
        """
        Register where a laundry's payouts should be sent.

        ``recipient_type`` is Paystack's transfer rail: 'ghipss' for Ghanaian
        bank accounts, 'mobile_money' for MoMo. The caller stores
        ``data.recipient_code``.
        """
        payload = {
            'type': recipient_type,
            'name': name,
            'account_number': account_number,
            'bank_code': bank_code,
            'currency': currency,
        }
        try:
            response = requests.post(
                f"{self.base_url}/transferrecipient",
                json=payload, headers=self.headers, timeout=15,
            )
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error("Paystack recipient creation error", extra={"error": summarize_exception(e)})
            return {'status': False, 'message': str(e)}

    def initiate_transfer(self, amount, recipient_code, reference, reason=''):
        """
        Send money to a recipient.

        ``amount`` is in cedis and converted to pesewas here. ``reference``
        must be stable for a given payout: Paystack rejects a duplicate
        reference, which is what stops a retry from paying a laundry twice.
        """
        payload = {
            'source': 'balance',
            'amount': to_minor_units(amount),
            'recipient': recipient_code,
            'reference': reference,
            'currency': 'GHS',
        }
        if reason:
            payload['reason'] = str(reason)[:100]

        try:
            response = requests.post(
                f"{self.base_url}/transfer",
                json=payload, headers=self.headers, timeout=20,
            )
            try:
                data = response.json()
            except ValueError:
                logger.error(
                    "Paystack transfer returned non-JSON response",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
                # Deliberately not treated as a failure by the caller: a
                # transfer whose outcome is unknown must not be retried blindly.
                return {'status': False, 'indeterminate': True, 'message': 'Invalid response from provider.'}
            if not response.ok:
                logger.error(
                    "Paystack transfer failed",
                    extra={"status_code": response.status_code, "message": data.get('message')},
                )
            return data
        except requests.exceptions.Timeout as e:
            # The request may well have been accepted. Report it as unknown so
            # the payout is left for a human rather than sent again.
            logger.error("Paystack transfer timed out", extra={"error": summarize_exception(e)})
            return {'status': False, 'indeterminate': True, 'message': 'Transfer request timed out.'}
        except requests.exceptions.RequestException as e:
            logger.error("Paystack transfer error", extra={"error": summarize_exception(e)})
            return {'status': False, 'message': str(e)}

    def list_banks(self, country='ghana'):
        """Supported banks and their codes, needed to create a subaccount."""
        try:
            response = requests.get(
                f"{self.base_url}/bank",
                params={'country': country},
                headers=self.headers,
                timeout=15,
            )
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error("Paystack bank list error", extra={"error": summarize_exception(e)})
            return {'status': False, 'message': str(e)}

    def initialize_transaction(
        self, email, amount, reference, metadata=None, subaccount=None,
        transaction_charge=None, bearer=None,
    ):
        """
        Initialize a payment transaction.
        Amount should be in minor currency units (e.g. pesewas for GHS).

        When ``subaccount`` is given, Paystack settles the transaction to that
        subaccount rather than the platform account. ``transaction_charge`` is
        the platform's flat cut in pesewas, and ``bearer`` decides who absorbs
        Paystack's own processing fee.
        """
        endpoint = f"{self.base_url}/transaction/initialize"
        payload = {
            'email': email,
            'amount': to_minor_units(amount),
            'currency': settings.PAYMENT_CURRENCY,
            'reference': reference,
            'metadata': metadata or {}
        }

        if subaccount:
            payload['subaccount'] = subaccount
            # Always sent, including zero: it overrides whatever percentage the
            # subaccount was created with, so the platform's cut is stated
            # explicitly on every transaction rather than inherited.
            payload['transaction_charge'] = int(transaction_charge or 0)
            if bearer:
                payload['bearer'] = bearer

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
                return {
                    'status': False,
                    'retryable': response.status_code >= 500,
                    'message': 'Payment provider returned an invalid response.',
                }
            if not response.ok:
                logger.error(
                    "Paystack initialization failed",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
                if response.status_code >= 500:
                    data['retryable'] = True
            return data
        except requests.exceptions.Timeout as e:
            logger.error("Paystack initialization timed out", extra={"error": summarize_exception(e)})
            return {
                'status': False,
                'indeterminate': True,
                'retryable': True,
                'message': 'Payment provider did not respond in time. Please retry safely.',
            }
        except requests.exceptions.RequestException as e:
            logger.error("Paystack request error", extra={"error": summarize_exception(e)})
            return {
                'status': False,
                'retryable': True,
                'message': 'Payment provider is temporarily unavailable.',
            }

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

    def refund_transaction(self, reference, amount=None, reason=None):
        """Ask Paystack to refund a settled transaction.

        `amount` is in major units (GHS) and defaults to a full refund when
        omitted. Paystack settles refunds asynchronously, so a successful
        response means *accepted*, not *completed* — the terminal state
        arrives later via the `refund.processed` webhook.
        """
        endpoint = f"{self.base_url}/refund"
        payload = {'transaction': reference}
        if amount is not None:
            payload['amount'] = to_minor_units(amount)
        if reason:
            payload['merchant_note'] = str(reason)[:200]

        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=self.headers,
                timeout=15,
            )
            try:
                data = response.json()
            except ValueError:
                logger.error(
                    "Paystack refund returned non-JSON response",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
                return {
                    'status': False,
                    'indeterminate': True,
                    'message': 'Payment provider returned an invalid response.',
                }
            if not response.ok:
                logger.error(
                    "Paystack refund failed",
                    extra={"status_code": response.status_code, "reference": mask_reference(reference)},
                )
                if response.status_code >= 500:
                    data['indeterminate'] = True
            return data
        except requests.exceptions.Timeout as e:
            logger.error(
                "Paystack refund timed out",
                extra={"reference": mask_reference(reference), "error": summarize_exception(e)},
            )
            return {
                'status': False,
                'indeterminate': True,
                'message': 'Refund outcome is unknown. Review it before retrying.',
            }
        except requests.exceptions.RequestException as e:
            logger.error(
                "Paystack refund error",
                extra={"reference": mask_reference(reference), "error": summarize_exception(e)},
            )
            return {
                'status': False,
                'indeterminate': True,
                'message': 'Refund outcome is unknown. Review it before retrying.',
            }
