"""Cross-check a structured extraction against an independent OCR transcription.

OCR.space is a second source of evidence, not a replacement. For each
structured row we find the OCR line(s) that mention the service and check that
its amount appears there:

    Gemini: Shirt -> 15      OCR: "Shirt .... 75"   => PRICE_PROVIDER_DISAGREEMENT

Flags (row level): PRICE_PROVIDER_DISAGREEMENT, SERVICE_NAME_MISMATCH,
PRICE_NOT_CONFIRMED.
Flags (document level): EXTRA_PRICE (OCR amounts no row accounts for:
possible missed services), CURRENCY_MISMATCH.
A row that passes the cross-check keeps its state. A row that fails can only
move towards review, never towards "looks good".
"""
from __future__ import annotations

import re
from decimal import Decimal

from .money import detect_currency, find_amounts
from .schema import DraftCandidate

_WORD_RE = re.compile(r'[a-z]{3,}')
_STOP = {'and', 'the', 'per', 'for', 'with', 'wash', 'iron', 'fold', 'clean', 'dry', 'press', 'item', 'piece', 'pcs'}


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or '').lower()))


def _key_words(text: str) -> set[str]:
    words = _words(text)
    core = words - _STOP
    return core or words


def _amount_of(c: DraftCandidate) -> Decimal | None:
    return c.price_per_kg if c.pricing_method == 'PER_KG' else (c.price if c.price is not None else c.price_per_kg)


def crosscheck(candidates: list[DraftCandidate], ocr_text: str, currency: str) -> dict:
    lines = [ln for ln in (ocr_text or '').splitlines() if ln.strip()]
    line_words = [_words(ln) for ln in lines]
    line_amounts = [find_amounts(ln) for ln in lines]
    all_ocr_amounts = [a for amounts in line_amounts for a in amounts]

    summary = {'checked': 0, 'agreed': 0, 'disagreed': 0, 'name_missing': 0, 'unconfirmed': 0}
    accounted: list[Decimal] = []
    for c in candidates:
        amount = _amount_of(c)
        if amount is None or not c.item_name:
            continue
        summary['checked'] += 1
        accounted.append(amount)
        wanted = _key_words(c.raw_name or c.item_name)
        matching = [i for i, words in enumerate(line_words) if wanted and wanted & words]
        if not matching:
            c.add_warning('SERVICE_NAME_MISMATCH')
            summary['name_missing'] += 1
            if amount not in all_ocr_amounts:
                c.add_warning('PRICE_NOT_CONFIRMED')
                summary['unconfirmed'] += 1
            continue
        amounts_near = [a for i in matching for a in line_amounts[i]]
        if amount in amounts_near:
            summary['agreed'] += 1
        elif amounts_near:
            c.add_warning('PRICE_PROVIDER_DISAGREEMENT')
            summary['disagreed'] += 1
        elif amount in all_ocr_amounts:
            summary['agreed'] += 1          # table layout: name and price on separate lines
        else:
            c.add_warning('PRICE_NOT_CONFIRMED')
            summary['unconfirmed'] += 1

    doc_warnings = []
    remaining = list(accounted)
    extra = 0
    for a in all_ocr_amounts:
        if a in remaining:
            remaining.remove(a)
        elif a >= 1:
            extra += 1
    if extra:
        doc_warnings.append('EXTRA_PRICE')
    ocr_currency = detect_currency(ocr_text or '')
    if (ocr_currency == 'GHS') != (currency == 'GHS'):
        doc_warnings.append('CURRENCY_MISMATCH')
    summary['extra_ocr_amounts'] = extra
    summary['doc_warnings'] = doc_warnings
    return summary
