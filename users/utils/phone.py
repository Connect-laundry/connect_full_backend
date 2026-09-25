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

# Ghana national numbers are 9 digits after the trunk 0: mobile 02x/05x
# (MTN 24/25/53/54/55/59, Telecel 20/50, AirtelTigo 26/27/56/57) and fixed
# lines 03x.
_GHANA_NATIONAL_RE = re.compile(r'^[235]\d{8}$')


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
        if digits.startswith('0'):
            # "+0245738120": a local number with a stray plus (seen in
            # production data). No country code starts with 0.
            digits = default_calling_code + digits[1:]
        e164 = '+' + digits
    else:
        if not cleaned.isdigit():
            raise PhoneValidationError('Phone number contains invalid characters.')
        if cleaned.startswith(default_calling_code) and len(cleaned) > len(default_calling_code) + 8:
            e164 = '+' + cleaned
        elif cleaned.startswith('0'):
            # National trunk format -> attach the default country code.
            e164 = '+' + default_calling_code + cleaned[1:]
        else:
            e164 = '+' + default_calling_code + cleaned

    # "+233 0245738120": the trunk 0 kept after the country code.
    if e164.startswith('+' + GHANA_CALLING_CODE + '0') and len(e164) == len(GHANA_CALLING_CODE) + 11:
        e164 = '+' + GHANA_CALLING_CODE + e164[len(GHANA_CALLING_CODE) + 2:]
    _validate_e164(e164)
    return e164


def format_phone_for_display(e164):
    """'+233245738120' -> '+233 24 573 8120' (other numbers unchanged)."""
    digits = (e164 or '').lstrip('+')
    if digits.startswith(GHANA_CALLING_CODE) and len(digits) == len(GHANA_CALLING_CODE) + 9:
        n = digits[len(GHANA_CALLING_CODE):]
        return f'+{GHANA_CALLING_CODE} {n[:2]} {n[2:5]} {n[5:]}'
    return e164 or ''


def to_ghana_momo_account_number(raw):
    """
    Return the 10-digit Ghana Mobile Money account number (e.g. '0551057139')
    expected by Paystack's transfer recipient API for type='mobile_money'.

    Accepts:
      - 0551057139
      - +233551057139
      - 233551057139
      - 055 105 7139
      - +233 55 105 7139
    """
    e164 = normalize_phone(raw)
    digits = e164.lstrip('+')
    if digits.startswith(GHANA_CALLING_CODE):
        national = digits[len(GHANA_CALLING_CODE):]
        return f"0{national}"
    raise PhoneValidationError('Only Ghanaian phone numbers can be used for Mobile Money payouts.')


def mask_phone_number(phone):
    """
    Mask a phone number for secure display, e.g. '055 *** 7139' or '+233 55 *** 7139'.
    Never discloses the full sensitive account digits in UI / logs.
    """
    if not phone:
        return ''
    cleaned = re.sub(r'[\s\-().]', '', str(phone))
    try:
        e164 = normalize_phone(cleaned)
        digits = e164.lstrip('+')
        if digits.startswith(GHANA_CALLING_CODE):
            nat = digits[len(GHANA_CALLING_CODE):]
            # nat is 9 digits, e.g. 551057139 -> '055 *** 7139'
            return f"0{nat[:2]} *** {nat[5:]}"
    except PhoneValidationError:
        pass

    # Generic masking fallback
    if len(cleaned) >= 7:
        return f"{cleaned[:3]} *** {cleaned[-4:]}"
    return '***'


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

