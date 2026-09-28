"""Admin recipient normalization on top of the shared Ghana phone normalizer.

Admin alerts carry customer PII, so a recipient must be a valid Ghana mobile
number. Anything that normalizes to another country is refused rather than
sent: a typo must never route customer addresses abroad.
"""
from users.utils.phone import GHANA_CALLING_CODE, PhoneValidationError, normalize_phone


class AdminRecipientError(ValueError):
    pass


def normalize_admin_recipient(raw) -> str:
    """'055 105 7139' / '+233551057139' / '233551057139' -> '233551057139'."""
    try:
        e164 = normalize_phone(raw)
    except PhoneValidationError as exc:
        raise AdminRecipientError(str(exc)) from exc
    digits = e164.lstrip('+')
    if not digits.startswith(GHANA_CALLING_CODE) or len(digits) != len(GHANA_CALLING_CODE) + 9:
        raise AdminRecipientError('Admin recipients must be Ghana numbers.')
    return digits


def mask_recipient(number: str) -> str:
    """'233551057139' -> '233 55 *** 7139' for admin screens and logs.

    Telegram chat ids (e.g. '-1001234567890') show only their last 4 digits.
    """
    digits = str(number or '')
    if digits.startswith('-') or (digits.isdigit() and not digits.startswith(GHANA_CALLING_CODE)):
        return f'chat ...{digits[-4:]}' if len(digits) >= 5 else '***'
    if len(digits) == 12 and digits.startswith(GHANA_CALLING_CODE):
        return f'{digits[:3]} {digits[3:5]} *** {digits[-4:]}'
    return '***'


def display_phone(raw) -> str:
    """Customer/laundry phone in the national form admins dial: '055 105 7139'."""
    if not raw:
        return ''
    try:
        e164 = normalize_phone(raw)
    except PhoneValidationError:
        return str(raw).strip()
    digits = e164.lstrip('+')
    if digits.startswith(GHANA_CALLING_CODE) and len(digits) == 12:
        national = '0' + digits[3:]
        return f'{national[:3]} {national[3:6]} {national[6:]}'
    return e164
