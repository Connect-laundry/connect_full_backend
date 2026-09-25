"""Match draft candidates against the laundry's existing pricing items.

EXACT: same name, ignoring case and spacing. Saving it would collide with
       the (laundry, item_name) unique constraint.
POSSIBLE: the same service written differently, e.g. "Shirt Wash and Iron"
          vs "Wash & Iron Shirt" (same token set), or a close fuzzy match.

Matching only annotates. The owner chooses per row whether to update the
existing item, create a new one, or ignore it. The AI never updates anything.
"""
from __future__ import annotations

import difflib
import re

from .schema import DraftCandidate

_SYNONYMS = {
    '&': ' and ', '+': ' and ', 'n': 'and', 'w/': 'with ',
    'shirts': 'shirt', 'trousers': 'trouser', 'pants': 'trouser', 'jeans': 'jean',
    'dresses': 'dress', 'suits': 'suit', 'duvets': 'duvet', 'curtains': 'curtain',
    'ironing': 'iron', 'washing': 'wash', 'folding': 'fold', 'drycleaning': 'dryclean',
    'pressing': 'press', 'pcs': 'piece', 'pc': 'piece', 'pieces': 'piece',
}
_FILLER = {'and', 'the', 'a', 'of', 'per', 'only', 'each', 'with'}


def name_tokens(name: str) -> tuple[str, ...]:
    text = (name or '').lower().replace('&', ' and ').replace('+', ' and ')
    text = text.replace('dry clean', 'dryclean').replace('dry-clean', 'dryclean')
    words = re.findall(r'[a-z0-9]+', text)
    words = [_SYNONYMS.get(w, w) for w in words]
    return tuple(sorted(w for w in words if w not in _FILLER))


def normalise_name(name: str) -> str:
    return re.sub(r'\s+', ' ', (name or '').strip().lower())


def match_candidates(candidates: list[DraftCandidate], existing_items) -> None:
    """``existing_items``: iterable of LaundryPricingItem."""
    existing = list(existing_items)
    by_exact = {normalise_name(i.item_name): i for i in existing}
    by_tokens: dict[tuple, object] = {}
    for item in existing:
        by_tokens.setdefault(name_tokens(item.item_name), item)

    for c in candidates:
        if c.pricing_method == 'PER_KG' or not c.item_name:
            continue
        hit = by_exact.get(normalise_name(c.item_name))
        match_type = 'EXACT' if hit else None
        if hit is None:
            tokens = name_tokens(c.item_name)
            hit = by_tokens.get(tokens) if tokens else None
            if hit is None and tokens:
                joined = ' '.join(tokens)
                best, best_ratio = None, 0.0
                for item in existing:
                    ratio = difflib.SequenceMatcher(None, joined, ' '.join(name_tokens(item.item_name))).ratio()
                    if ratio > best_ratio:
                        best, best_ratio = item, ratio
                if best is not None and best_ratio >= 0.88:
                    hit = best
            match_type = 'POSSIBLE' if hit else None
        if hit is None:
            continue
        c.match_type = match_type
        c.matched_item_id = str(hit.id)
        c.matched_item_name = hit.item_name
        c.matched_item_price = hit.unit_price
        c.add_warning('EXISTING_ITEM_MATCH' if match_type == 'EXACT' else 'POSSIBLE_MATCH')
        if c.review_state == 'LOOKS_GOOD':
            c.review_state = 'CHECK'
