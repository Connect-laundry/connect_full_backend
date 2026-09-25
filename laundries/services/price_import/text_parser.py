"""Deterministic parser: plain OCR transcription -> structured candidates.

Used when Gemini is unavailable (fallback) and never trusted on its own: every
row it produces is marked for owner review. It reads:

* dot-leader / dash / colon lines: ``Shirt ........ GH¢ 15``
* tab/whitespace tables (OCR.space ``isTable=true`` emits tab-separated cells)
  with an optional header row of service types (``Wash & Iron | Dry Clean``)
* per-kg lines: ``Wash & Fold 18/kg``, ``1kg = 18``, ``GH¢18 per kg``
* section headings (a short line with no amounts) as the category
* a name on its own line directly followed by a line holding only its price
  (OCR often splits dot-leader rows that way)

It never invents a pairing: a row with several amounts and no matching header
becomes several candidates flagged AMBIGUOUS_PAIRING.
"""
from __future__ import annotations

import re

from .money import _AMOUNT_RE, _CEDI_RE, _TRAILING_SLASH_RE, detect_currency, mentions_per_kg
from .schema import PriceListExtraction, ServiceCandidate

_CELL_SPLIT_RE = re.compile(r'\t+|\s{3,}|\.{2,}|…+|\s[|]\s|_{2,}')
_SERVICE_WORDS = re.compile(
    r'\b(wash|iron|press|dry\s*clean|fold|steam|starch|express|normal|regular|same\s*day|'
    r'single|double|queen|king|small|medium|large|big)\b',
    re.IGNORECASE,
)
_LETTERS_RE = re.compile(r'[A-Za-z]{2,}')
_KG_ONLY_RE = re.compile(r'^\s*1\s*kg\s*[=:\-]?\s*', re.IGNORECASE)
_NOISE_CHARS = ' .-–—=:_~*|•·\t'
PARSER_CONFIDENCE = 0.5
_TITLE_RE = re.compile(r'\b(price\s*list|prices|menu|tariff|rates|tel|phone|whatsapp|call\s+us|open)\b', re.IGNORECASE)
_SHOP_RE = re.compile(r'(laundr|cleaner|wash\s*house|launderette|laundromat|\bltd\b|enterprise|services)', re.IGNORECASE)


def _is_amount_cell(cell: str) -> bool:
    stripped = _CEDI_RE.sub('', cell)
    stripped = re.sub(r'/\s*(kg|pc|pcs|piece)\b|\bper\s*kg\b|\bkg\b|/\s*[-=]', '', stripped, flags=re.IGNORECASE)
    stripped = stripped.strip(_NOISE_CHARS + '()')
    return bool(stripped) and bool(re.fullmatch(r'[\d.,\s]+', stripped)) and bool(_AMOUNT_RE.search(stripped))


def _clean_name(text: str) -> str:
    text = _CEDI_RE.sub('', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(_NOISE_CHARS + '(').strip()


def _split_cells(line: str) -> list[str]:
    return [c for c in (p.strip() for p in _CELL_SPLIT_RE.split(line)) if c]


def _header_cells(line: str) -> list[str] | None:
    cells = _split_cells(line)
    if len(cells) >= 2 and not _AMOUNT_RE.search(line):
        service_cells = [c for c in cells if _SERVICE_WORDS.search(c)]
        if len(service_cells) >= 2:
            return service_cells
    return None


def _looks_like_heading(line: str) -> bool:
    words = line.split()
    return (
        0 < len(words) <= 4
        and not _AMOUNT_RE.search(line)
        and bool(_LETTERS_RE.search(line))
        and (line.isupper() or line.rstrip().endswith(':'))
    )


def parse_text(raw_text: str) -> PriceListExtraction:
    items: list[ServiceCandidate] = []
    warnings: list[str] = []
    category: str | None = None
    headers: list[str] | None = None
    document_currency = 'GHS' if detect_currency(raw_text or '') == 'GHS' else None
    pending_name: str | None = None

    for raw_line in (raw_text or '').splitlines():
        line = _TRAILING_SLASH_RE.sub(r'\1', raw_line.replace('\r', '')).rstrip()
        if not line.strip():
            continue
        # A name only pairs with the price on the very next line.
        previous_name, pending_name = pending_name, None

        hdr = _header_cells(line)
        if hdr:
            headers = hdr
            continue
        if _looks_like_heading(line.strip()):
            # Titles ("PRICE LIST", the shop name) are not categories.
            if not _TITLE_RE.search(line) and not (not items and _SHOP_RE.search(line)):
                category = _clean_name(line).title()[:80] or None
            headers = None
            continue

        cells = _split_cells(line)
        name_cells = [c for c in cells if not _is_amount_cell(c)]
        price_cells = [c for c in cells if _is_amount_cell(c)]

        if not price_cells:
            # Single-cell line: "Wash & Fold 18/kg", "Shirt 15", "1kg = 18".
            m = re.match(
                r'^(?P<name>.*?[A-Za-z].*?)\s*[:=\-–]?\s*(?P<price>(?:GH\S*\s*|₵\s*|¢\s*)?\d[\d.,]*\s*(?:/\s*kg|per\s*kg|kg|cedis?)?)\s*$',
                line.strip(), re.IGNORECASE,
            )
            kg_line = _KG_ONLY_RE.match(line)
            if kg_line:
                name_cells, price_cells = ['Per kg'], [line[kg_line.end():]]
            elif m and _LETTERS_RE.search(m.group('name')):
                name_cells, price_cells = [m.group('name')], [m.group('price')]
            else:
                pending_name = _standalone_name(line)
                continue

        use_headers = headers if headers and len(headers) == len(price_cells) else None
        # Multi-column lists come back as one line per visual row holding
        # several "name .... price" pairs. When cells strictly alternate
        # name, amount, name, amount, pair them in order.
        if not use_headers and len(price_cells) > 1 and len(name_cells) == len(price_cells):
            kinds = [_is_amount_cell(c) for c in cells]
            if kinds == [False, True] * len(price_cells):
                for name_cell, price_cell in zip(name_cells, price_cells):
                    items.extend(_row_items(_clean_name(name_cell), [price_cell], None, category, line,
                                            per_kg_line=mentions_per_kg(price_cell)))
                continue
        # Messier multi-column OCR: a price and the next item's name share a
        # cell ("GH¢ 22 Bath Robe"). Scan name→amount pairs in reading order.
        if not use_headers:
            pairs = _scan_pairs(line)
            if len(pairs) >= 2:
                for pair_name, amount_text, per_kg in pairs:
                    items.extend(_row_items(pair_name, [amount_text + ('/kg' if per_kg else '')], None,
                                            category, line, per_kg_line=per_kg))
                continue

        name = _clean_name(' '.join(name_cells))
        if not name or not _LETTERS_RE.search(name):
            if previous_name and len(price_cells) == 1 and len(_AMOUNT_RE.findall(price_cells[0])) == 1:
                items.extend(_row_items(previous_name, price_cells, None, category,
                                        f'{previous_name} {line.strip()}', per_kg_line=mentions_per_kg(line)))
                continue
            warnings.append('UNPAIRED_AMOUNTS')
            continue
        items.extend(_row_items(name, price_cells, use_headers, category, line,
                                per_kg_line=mentions_per_kg(line)))

    if 'UNPAIRED_AMOUNTS' in warnings:
        warnings = ['UNPAIRED_AMOUNTS']
    return PriceListExtraction(
        currency=document_currency,
        items=items,
        warnings=warnings,
        document_confidence=PARSER_CONFIDENCE if items else 0.0,
    )


def _standalone_name(line: str) -> str | None:
    """A short item name alone on its line ("Shirt", "Bedsheet (double)")."""
    name = _clean_name(line)
    if not name or not _LETTERS_RE.search(name) or len(name.split()) > 5 or _TITLE_RE.search(name):
        return None
    return name


_PAIR_RE = re.compile(
    r'(?P<name>[A-Za-z](?:[^\t\d(]|\([^)\t]*\))*?)[\s.…_:=\-–]*'
    r'(?:\bGH\s?(?:[^\w\s]|[^\x00-\x7f])|\bGH[SC](?![A-Za-z])|₵|¢)?\s*'
    r'(?P<amt>\d+(?:[.,]\d{1,2})?)'
    # an amount inside brackets is part of the name: "Suit (2 piece)"
    r'(?![\d])(?!\s*\w*\))'
    r'(?P<kg>\s*(?:/\s*kgs?\b|per\s*kgs?\b))?',
    re.IGNORECASE,
)


def _scan_pairs(line: str) -> list[tuple[str, str, bool]]:
    pairs = []
    for m in _PAIR_RE.finditer(line):
        name = _clean_name(m.group('name'))
        if name and _LETTERS_RE.search(name) and not _CEDI_RE.fullmatch(name.strip()):
            pairs.append((name, m.group('amt'), bool(m.group('kg'))))
    return pairs


def _row_items(name, price_cells, use_headers, category, line, *, per_kg_line) -> list[ServiceCandidate]:
    items: list[ServiceCandidate] = []
    if not name or not _LETTERS_RE.search(name):
        return items
    for index, cell in enumerate(price_cells):
        amount = _AMOUNT_RE.search(_CEDI_RE.sub('', cell))
        if not amount:
            continue
        amount_text = amount.group(0)
        row_warnings = ['PARSED_FROM_OCR_TEXT']
        variant = None
        if use_headers:
            variant = use_headers[index][:80]
        elif len(price_cells) > 1:
            row_warnings.append('AMBIGUOUS_PAIRING')
        per_kg = mentions_per_kg(cell) or (per_kg_line and len(price_cells) == 1)
        if per_kg:
            method, price, price_per_kg = 'PER_KG', None, amount_text
        elif len(price_cells) == 1 or use_headers:
            method, price, price_per_kg = 'PER_ITEM', amount_text, None
            row_warnings.append('PRICING_METHOD_INFERRED')
        else:
            method, price, price_per_kg = 'UNKNOWN', amount_text, None
        items.append(ServiceCandidate(
            raw_name=name[:200],
            normalized_name=name.title()[:120],
            category=category,
            variant=variant,
            pricing_method=method,
            price=price,
            price_per_kg=price_per_kg,
            source_text=line.strip()[:300],
            confidence=PARSER_CONFIDENCE,
            warnings=row_warnings,
        ))
    return items
