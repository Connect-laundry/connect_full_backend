"""Phone-number normalization and validation (E.164).

Dependency-free (no `phonenumbers` package required). Focused on Ghana
(+233) mobile numbers — the platform's operating market — with a generic
E.164 fallback for other country codes so international numbers are not
rejected outright.

Kept intentionally small and mirrored by the mobile client's
``src/utils/phoneValidation.ts`` so both sides normalize identically before a
number ever reaches the database.
"""
import re

GHANA_CALLING_CODE = '233'

# Ghana mobile national numbers are 9 digits and start with 2 or 5
# (02x / 05x prefixes once the trunk 0 is dropped).
_GHANA_NATIONAL_RE = re.compile(r'^[25]\d{8}$')


class PhoneValidationError(ValueError):
    """Raised when a phone number cannot be normalized to a valid E.164 form."""


def normalize_phone(raw, default_calling_code=GHANA_CALLING_CODE):
    """Return the E.164 form of ``raw`` (e.g. ``0241234567`` -> ``+233241234567``).

    Raises :class:`PhoneValidationError` for empty or invalid input. Idempotent:
    an already-normalized ``+233...`` value is returned unchanged.
    """
    if raw is None:
        raise PhoneValidationError('Phone number is required.')

    # Strip common formatting characters (spaces, dashes, dots, parentheses).
    cleaned = re.sub(r'[\s\-().]', '', str(raw))
    if not cleaned:
        raise PhoneValidationError('Phone number is required.')

    # International prefix 00 -> +
    if cleaned.startswith('00'):
        cleaned = '+' + cleaned[2:]

    if cleaned.startswith('+'):
        digits = cleaned[1:]
        if not digits.isdigit():
            raise PhoneValidationError('Phone number contains invalid characters.')
        e164 = '+' + digits
    else:
        if not cleaned.isdigit():
            raise PhoneValidationError('Phone number contains invalid characters.')
        if cleaned.startswith(default_calling_code):
            e164 = '+' + cleaned
        elif cleaned.startswith('0'):
            # National trunk format -> attach the default country code.
            e164 = '+' + default_calling_code + cleaned[1:]
        else:
            e164 = '+' + default_calling_code + cleaned

    _validate_e164(e164)
    return e164


def _validate_e164(e164):
    digits = e164[1:]
    if not digits.isdigit():
        raise PhoneValidationError('Enter a valid phone number.')

    # Any number resolving to Ghana must satisfy the national mobile rule.
    if digits.startswith(GHANA_CALLING_CODE):
        national = digits[len(GHANA_CALLING_CODE):]
        if not _GHANA_NATIONAL_RE.match(national):
            raise PhoneValidationError('Enter a valid Ghana phone number.')
        return

    # Generic E.164 bounds for other regions (ITU: up to 15 digits).
    if not (8 <= len(digits) <= 15):
        raise PhoneValidationError('Enter a valid phone number.')
