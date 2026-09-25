"""Deterministic validation of provider output. AI output is never trusted directly.

Turns schema-valid ``ServiceCandidate`` rows into ``DraftCandidate`` rows and
assigns each an owner-facing review state:

* LOOKS_GOOD: name and price read cleanly, pricing method known, evidence in
  ``source_text`` contains the price, no warnings, currency confirmed.
* CHECK: anything ambiguous or corrected, or only one source read it.
* UNREADABLE: no usable price, or no usable name.

Values are never "fixed". An impossible value is nulled and flagged; it is not
rescaled or guessed.
"""
from __future__ import annotations

import re
from decimal import Decimal

from django.conf import settings

from .money import find_amounts, parse_amount
from .schema import DraftCandidate, PriceListExtraction, ServiceCandidate

LOOKS_GOOD, CHECK, UNREADABLE = 'LOOKS_GOOD', 'CHECK', 'UNREADABLE'

# Codes that are informational only and do not by themselves demand a check.
_BENIGN_WARNINGS = frozenset()

# Text that reads like an instruction or exfiltration attempt rather than a
# garment/service. Rows matching are deselected and flagged; the owner can
# still re-select them if they really are services.
_INJECTION_RE = re.compile(
    r'(ignore\s+(all\s+)?(previous|prior|above)|instruction|system\s*prompt|api[\s_-]*key|'
    r'password|secret|token|admin|https?://|www\.|\bcall\s+this\b|\bdelete\b|\bset\s+all\b|'
    r'\bprompt\b|<\s*script|\{\s*"|\bjson\b|\bsudo\b|\bexecute\b)',
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f<>{}`\\]')
MIN_CONFIDENCE_FOR_GOOD = 0.8
MIN_CONFIDENCE = 0.6


def max_price() -> Decimal:
    return Decimal(str(settings.PRICE_LIST_MAX_PRICE))


def clean_text(value: str | None, limit: int) -> str:
    if not value:
        return ''
    value = _CONTROL_RE.sub(' ', str(value))
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def compose_item_name(name: str, variant: str) -> str:
    if variant and variant.lower() not in name.lower():
        return f'{name} – {variant}'[:120]
    return name[:120]


def _parse_money_field(candidate: DraftCandidate, raw: str | None, label: str) -> Decimal | None:
    if raw is None or str(raw).strip() == '':
        return None
    parsed = parse_amount(raw)
    for w in parsed.warnings:
        candidate.add_warning(w if label == 'price' else f'{label.upper()}_{w}')
    if parsed.foreign_currency:
        candidate.add_warning('FOREIGN_CURRENCY')
    value = parsed.value
    if value is None:
        return None
    if value < 0:
        candidate.add_warning('NEGATIVE_PRICE')
        return None
    if value == 0:
        candidate.add_warning('ZERO_PRICE')
    if value > max_price():
        candidate.add_warning('SUSPICIOUS_PRICE')
    return value


def _evidence_has(value: Decimal | None, source_text: str) -> bool:
    if value is None:
        return True
    return any(a == value for a in find_amounts(source_text))


def normalise_candidate(row: ServiceCandidate) -> DraftCandidate:
    raw_name = clean_text(row.raw_name, 200)
    base_name = clean_text(row.normalized_name, 120) or raw_name[:120]
    variant = clean_text(row.variant, 80)
    candidate = DraftCandidate(
        raw_name=raw_name,
        item_name=compose_item_name(base_name, variant) if base_name else '',
        category=clean_text(row.category, 80),
        variant=variant,
        pricing_method=row.pricing_method,
        source_text=clean_text(row.source_text, 300),
        confidence=round(float(row.confidence), 3),
    )
    for w in row.warnings or []:
        code = re.sub(r'[^A-Z0-9_]', '', str(w).upper().replace(' ', '_'))[:40]
        if code:
            candidate.add_warning(code)

    candidate.price = _parse_money_field(candidate, row.price, 'price')
    candidate.price_per_kg = _parse_money_field(candidate, row.price_per_kg, 'price_per_kg')
    candidate.surcharge_type = clean_text(row.surcharge_type, 40)
    candidate.surcharge_amount = _parse_money_field(candidate, row.surcharge_amount, 'surcharge')

    # Pricing-method consistency. Never guess to satisfy the schema.
    method = candidate.pricing_method
    if method == 'PER_KG' and candidate.price_per_kg is None and candidate.price is not None:
        # The amount landed in the wrong field; keep the value, flag the move.
        candidate.price_per_kg, candidate.price = candidate.price, None
        candidate.add_warning('PRICE_FIELD_MOVED')
    elif method == 'PER_ITEM' and candidate.price is None and candidate.price_per_kg is not None:
        candidate.pricing_method = 'UNKNOWN'
        candidate.add_warning('PRICING_METHOD_CONFLICT')
    elif method == 'UNKNOWN':
        candidate.add_warning('PRICING_METHOD_UNKNOWN')

    amount = candidate.price_per_kg if candidate.pricing_method == 'PER_KG' else candidate.price
    if amount is None and candidate.pricing_method == 'UNKNOWN':
        amount = candidate.price if candidate.price is not None else candidate.price_per_kg
    if amount is None:
        candidate.add_warning('MISSING_PRICE')
    elif not _evidence_has(amount, candidate.source_text):
        # Anti-hallucination: the printed evidence must contain the amount.
        candidate.add_warning('PRICE_NOT_IN_SOURCE_TEXT')

    if not candidate.item_name:
        candidate.add_warning('MISSING_NAME')
    elif _INJECTION_RE.search(candidate.raw_name) or _INJECTION_RE.search(candidate.item_name):
        candidate.add_warning('SUSPICIOUS_TEXT')
        candidate.is_selected = False

    if candidate.confidence is not None and candidate.confidence < MIN_CONFIDENCE:
        candidate.add_warning('LOW_CONFIDENCE')
    return candidate


def assign_review_state(candidate: DraftCandidate, *, currency_confirmed: bool, trusted_source: bool) -> None:
    hard_missing = {'MISSING_PRICE', 'MISSING_NAME'} & set(candidate.warnings)
    if hard_missing:
        candidate.review_state = UNREADABLE
        return
    blocking = [w for w in candidate.warnings if w not in _BENIGN_WARNINGS]
    good = (
        not blocking
        and trusted_source
        and currency_confirmed
        and candidate.pricing_method in ('PER_ITEM', 'PER_KG')
        and (candidate.confidence or 0) >= MIN_CONFIDENCE_FOR_GOOD
    )
    candidate.review_state = LOOKS_GOOD if good else CHECK


def _dedupe(candidates: list[DraftCandidate]) -> None:
    seen: dict[tuple, DraftCandidate] = {}
    for c in candidates:
        key = (c.item_name.lower(), c.pricing_method)
        if not c.item_name:
            continue
        if key in seen:
            first = seen[key]
            if (first.price, first.price_per_kg) == (c.price, c.price_per_kg):
                c.add_warning('DUPLICATE_ROW')      # exact repeat: keep the first
                c.is_selected = False
            else:
                first.add_warning('CONFLICTING_DUPLICATE')
                c.add_warning('CONFLICTING_DUPLICATE')
        else:
            seen[key] = c


def validate_extraction(extraction: PriceListExtraction, *, trusted_source: bool) -> tuple[list[DraftCandidate], list[str], str]:
    """Return (candidates, document_warnings, currency)."""
    doc_warnings: list[str] = []
    for w in extraction.warnings or []:
        code = re.sub(r'[^A-Z0-9_]', '', str(w).upper().replace(' ', '_'))[:40]
        if code and code not in doc_warnings:
            doc_warnings.append(code)

    candidates = [normalise_candidate(row) for row in extraction.items]

    # Currency: GHS only on evidence. A cedi marker inside any row counts.
    currency = 'GHS' if extraction.currency == 'GHS' else ''
    if not currency and any(
        parse_amount(c.source_text).currency_marker for c in candidates if c.source_text
    ):
        currency = 'GHS'
    if not currency:
        doc_warnings.append('CURRENCY_UNCONFIRMED')
    if any('FOREIGN_CURRENCY' in c.warnings for c in candidates):
        doc_warnings.append('FOREIGN_CURRENCY')

    per_kg = [c for c in candidates if c.pricing_method == 'PER_KG']
    if len(per_kg) > 1:
        # Simame stores ONE per-kg tariff per laundry; the owner must choose.
        for c in per_kg:
            c.add_warning('MULTIPLE_PER_KG_RATES')

    _dedupe(candidates)
    for c in candidates:
        assign_review_state(c, currency_confirmed=bool(currency), trusted_source=trusted_source)
    if not trusted_source:
        doc_warnings.append('FALLBACK_TEXT_PARSER_USED')
    return candidates, doc_warnings, currency
