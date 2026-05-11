from django.test import RequestFactory

from config.logging_formatters import CustomJsonFormatter
from config.redaction import mask_email, mask_phone, mask_reference, redact_value


def test_mask_email_preserves_domain_only():
    assert mask_email('customer@example.com') == 'c***@example.com'


def test_mask_phone_only_leaks_last_digits():
    assert mask_phone('+233501234567') == '***4567'


def test_mask_reference_hides_middle_characters():
    assert mask_reference('PAYSTACK-123456789') == 'PAY***789'


def test_redact_value_recursively_removes_sensitive_fields():
    payload = {
        'email': 'customer@example.com',
        'nested': {
            'authorization': 'Bearer secret',
            'payment_reference': 'PAYSTACK-123456789',
            'phone_number': '+233501234567',
        },
    }

    assert redact_value(payload) == {
        'email': 'c***@example.com',
        'nested': {
            'authorization': '[REDACTED]',
            'payment_reference': 'PAY***789',
            'phone_number': '***4567',
        },
    }


def test_logging_formatter_tolerates_requests_without_user():
    request = RequestFactory().get('/health/')
    formatter = CustomJsonFormatter()
    log_record = {'request': request}

    formatter.add_fields(log_record, type('Record', (), {'levelname': 'ERROR'})(), {})

    assert log_record['path'] == '/health/'
    assert log_record['user_id'] == 'Anonymous'
