"""OCR Parsing Intelligence for pricing catalogs.

Processes raw text extracted from price list images and parses them into
structured draft items with item names, prices, categories, and confidence scores.
"""

from decimal import Decimal, InvalidOperation
import logging
import re

logger = logging.getLogger(__name__)

# Pattern to match: Name ... [Currency] Price [Currency]
# Group 1: Item Name
# Group 2: Price value (digits optionally followed by dot or comma and decimals)
# Separator must be 2+ dots or any combination of other separators to avoid matching decimal points as separators.
STRICT_PRICE_REGEX = re.compile(
    r'^(?P<name>.+?)\s*(?:\.{2,}|[\-\–\—\=\_\:\~]+)\s*(?:GHS|GH¢|GHC|[\$¢€£¥])?\s*(?P<price>\d+(?:[\.,]\d{1,2})?)\s*(?:GHS|GH¢|GHC|[\$¢€£¥])?\s*$',
    re.IGNORECASE
)

# Fallback pattern for lines without clear separators but containing a trailing number
FALLBACK_PRICE_REGEX = re.compile(
    r'^(?P<name>.+?)\s+(?:GHS|GH¢|GHC|[\$¢€£¥])?\s*(?P<price>\d+(?:[\.,]\d{1,2})?)\s*(?:GHS|GH¢|GHC|[\$¢€£¥])?\s*$',
    re.IGNORECASE
)


# Keywords to match standard laundry categories
CATEGORY_KEYWORDS = {
    'Shirts': ['shirt', 't-shirt', 'top', 'blouse', 'polo'],
    'Trousers': ['trouser', 'pants', 'jeans', 'shorts', 'suit trouser'],
    'Dresses': ['dress', 'gown', 'skirt', 'frock'],
    'Suits': ['suit', 'blazer', 'tuxedo', 'coat', 'jacket'],
    'Bedding': ['bedding', 'sheet', 'duvet', 'blanket', 'pillow', 'quilt', 'bedspread'],
    'Curtains': ['curtain', 'drape'],
    'Shoes': ['shoe', 'sneaker', 'boot', 'footwear', 'sandal'],
    'Household': ['household', 'towel', 'rug', 'mat', 'cloth', 'napkin', 'tablecloth']
}


def resolve_category_from_name(item_name: str) -> str:
    """Determine the default category based on name keywords, defaulting to 'Shirts'."""
    name_lower = item_name.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in name_lower for keyword in keywords):
            return cat
    return 'Shirts'


def clean_price_string(price_str: str) -> Decimal | None:
    """Normalize decimal separator and return Decimal value or None if invalid."""
    if not price_str:
        return None
    # Replace comma with dot for decimal notation
    normalized = price_str.replace(',', '.')
    try:
        # Strip leading zeroes to avoid octal/invalid parses
        normalized = normalized.lstrip('0')
        if not normalized or normalized.startswith('.'):
            normalized = '0' + normalized
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


def clean_name_string(name_str: str) -> str:
    """Strip out remnants of separators, currencies, and extra spacing."""
    # Remove leading/trailing non-alphanumeric noise (except brackets/quotes)
    cleaned = name_str.strip(' .-–—=:_~*')
    # Remove double spaces
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned


def parse_ocr_text(raw_text: str) -> list[dict]:
    """Parse raw OCR document text block into candidate draft pricing items.

    Args:
        raw_text: The full text string returned by the OCR engine.

    Returns:
        list[dict]: List of parsed items with keys: item_name, suggested_price, category, confidence.
    """
    if not raw_text:
        return []

    lines = raw_text.split('\n')
    candidates = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Skip headers or meta info
        if len(line_stripped) < 3:
            continue

        item_name = None
        price_val = None
        confidence = 0.5

        # 1. Try strict parsing with dotted/dashed separators
        match = STRICT_PRICE_REGEX.match(line_stripped)
        if match:
            item_name = clean_name_string(match.group('name'))
            price_val = clean_price_string(match.group('price'))
            # Dotted lines with clean decimal are high confidence
            confidence = 0.95 if '.' in match.group('price') or ',' in match.group('price') else 0.85
        else:
            # 2. Try fallback parsing with space separator
            match = FALLBACK_PRICE_REGEX.match(line_stripped)
            if match:
                item_name = clean_name_string(match.group('name'))
                price_val = clean_price_string(match.group('price'))
                confidence = 0.80 if '.' in match.group('price') or ',' in match.group('price') else 0.70

        # Validate name and price before adding candidate
        if item_name and len(item_name) >= 2 and price_val is not None:
            # Prevent absurd prices or negative numbers
            if price_val < 0 or price_val > 10000:
                continue

            category = resolve_category_from_name(item_name)
            candidates.append({
                'item_name': item_name,
                'suggested_price': price_val,
                'category': category,
                'confidence': confidence
            })
        else:
            logger.debug("Skipping line: %r (Could not parse item name or price)", line_stripped)

    logger.info("Parsed %d candidates from raw text of %d lines.", len(candidates), len(lines))
    return candidates
