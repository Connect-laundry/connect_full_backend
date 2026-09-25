"""Deterministic Ghana-cedi amount parsing.

AI output is never trusted to have produced a clean number: providers return the
amount *as printed* and this module turns it into a ``Decimal`` (never float).

Rules that matter:
* ``15.00`` is fifteen, never 1500 — a dot followed by 1-2 digits is a decimal.
* ``1,500`` is fifteen hundred — a comma followed by exactly three digits is a
  thousands separator. ``15,50`` (comma + 1-2 digits) is read as a decimal
  comma but flagged, because it is ambiguous.
* Ranges (``15-20``), multiple amounts, or no digits yield ``None`` + a warning:
  we never guess which number was meant.
* A bare number is not assumed to be cedis; currency is detected separately.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# Cedi markers: GH₵ / GH¢ / GHS / GHC / ₵ / ¢ / "cedi(s)".
# "GH" followed by any symbol is the cedi sign as printed or as OCR mangles it.
# Seen live from OCR.space: "GH�", "GH$", "GHф", "GH₫". "GH" is Ghana
# specific, so a symbol after it is never a foreign currency.
_CEDI_RE = re.compile(
    r'(\bGH\s?(?:[^\w\s]|[^\x00-\x7f])|\bGH[SC](?![A-Za-z])|₵|¢|\bcedis?\b|\bGH\b)',
    re.IGNORECASE,
)
_FOREIGN_RE = re.compile(r'((?<!GH)(?<!GH )[$€£]|\bUSD\b|\bEUR\b|\bGBP\b|\bNGN\b|₦)', re.IGNORECASE)

# A single amount token: 1,500.00 | 1500 | 15.5 | 15,50
_AMOUNT_RE = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\d])')
_RANGE_RE = re.compile(r'\d\s*(?:-|–|—|to)\s*(?:GH\S*\s*)?\d', re.IGNORECASE)
# Ghanaian price notation: "15/-" or "15/=".
_TRAILING_SLASH_RE = re.compile(r'(\d)\s*/\s*[-=]')
_PER_KG_RE = re.compile(r'(/\s*kgs?\b|\bper\s*kgs?\b|(?<![A-Za-z])kgs?\b|\bkilo(?:gram)?s?\b)', re.IGNORECASE)

MAX_DIGITS_BEFORE_POINT = 8


@dataclass
class ParsedAmount:
    value: Decimal | None
    warnings: list[str] = field(default_factory=list)
    currency_marker: bool = False
    foreign_currency: bool = False


def detect_currency(text: str) -> str | None:
    """'GHS' when a cedi marker is present, 'FOREIGN' for another currency,
    else None (uncertain)."""
    if not text:
        return None
    if _CEDI_RE.search(text):
        return 'GHS'
    if _FOREIGN_RE.search(text):
        return 'FOREIGN'
    return None


def mentions_per_kg(text: str) -> bool:
    return bool(text and _PER_KG_RE.search(text))


def find_amounts(text: str) -> list[Decimal]:
    """Every distinct amount token in ``text`` (for cross-checking), in order."""
    if not text:
        return []
    cleaned = _TRAILING_SLASH_RE.sub(r'\1', text)
    out: list[Decimal] = []
    for token in _AMOUNT_RE.findall(cleaned):
        value = _token_to_decimal(token)[0]
        if value is not None:
            out.append(value)
    return out


def parse_amount(text: str | None) -> ParsedAmount:
    """Parse one printed amount. ``None`` value when absent or ambiguous."""
    if text is None:
        return ParsedAmount(None)
    raw = str(text).strip()
    if not raw:
        return ParsedAmount(None)

    result = ParsedAmount(None)
    result.currency_marker = bool(_CEDI_RE.search(raw))
    result.foreign_currency = bool(_FOREIGN_RE.search(raw))
    if re.match(r'^[\s(]*[-−–]\s*\d', _CEDI_RE.sub('', raw)):
        result.warnings.append('NEGATIVE_PRICE')
        return result
    if _RANGE_RE.search(raw):
        result.warnings.append('PRICE_RANGE')
        return result

    cleaned = _TRAILING_SLASH_RE.sub(r'\1', raw)
    # Drop per-kg / per-piece suffixes so "18/kg" parses as 18.
    cleaned = re.sub(r'/\s*(kg|pc|pcs|piece|item)\b', ' ', cleaned, flags=re.IGNORECASE)
    tokens = _AMOUNT_RE.findall(cleaned)
    if not tokens:
        result.warnings.append('PRICE_UNREADABLE')
        return result
    if len(tokens) > 1:
        result.warnings.append('MULTIPLE_AMOUNTS')
        return result

    value, token_warnings = _token_to_decimal(tokens[0])
    result.warnings.extend(token_warnings)
    result.value = value
    return result


def _token_to_decimal(token: str) -> tuple[Decimal | None, list[str]]:
    warnings: list[str] = []
    t = token
    if re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?', t):
        t = t.replace(',', '')                 # thousands separators
    elif re.fullmatch(r'\d+,\d{1,2}', t):
        t = t.replace(',', '.')                # decimal comma: ambiguous
        warnings.append('AMBIGUOUS_DECIMAL_SEPARATOR')
    integer_part = t.split('.')[0]
    if len(integer_part) > MAX_DIGITS_BEFORE_POINT:
        return None, ['PRICE_UNREADABLE']
    try:
        value = Decimal(t).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None, ['PRICE_UNREADABLE']
    return value, warnings
