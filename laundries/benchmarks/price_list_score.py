"""Scoring for price-list extraction benchmarks.

Primary metric: SERVICE-PRICE PAIRING ACCURACY. A row counts as paired
correctly only if the predicted row for that service (name + variant) carries
the ground-truth amount. Reading every digit correctly but attaching GHS 15 to
the wrong service is a failure.
"""
from __future__ import annotations

import difflib
import statistics
from decimal import Decimal

from laundries.services.price_import.matching import name_tokens


def _key(name: str, variant: str | None) -> str:
    return ' '.join(name_tokens(f'{name} {variant or ""}'))


def _pred_amount(c) -> Decimal | None:
    if c.pricing_method == 'PER_KG':
        return c.price_per_kg
    return c.price if c.price is not None else c.price_per_kg


def score_image(truth: dict, candidates, currency: str) -> dict:
    """``candidates``: list of DraftCandidate from validation."""
    truth_rows = truth['rows']
    preds = list(candidates)
    used: set[int] = set()
    pairs = []
    for t in truth_rows:
        tkey = _key(t['name'], t['variant'])
        best, best_ratio = None, 0.0
        for i, c in enumerate(preds):
            if i in used:
                continue
            pkey = _key(c.item_name or c.raw_name, None)
            ratio = 1.0 if pkey == tkey else difflib.SequenceMatcher(None, pkey, tkey).ratio()
            if ratio > best_ratio:
                best, best_ratio = i, ratio
        if best is not None and best_ratio >= 0.8:
            used.add(best)
            pairs.append((t, preds[best]))
        else:
            pairs.append((t, None))

    truth_amounts = [Decimal(t['price']) for t in truth_rows]
    matched = [(t, c) for t, c in pairs if c is not None]
    correct_pairs = sum(1 for t, c in matched if _pred_amount(c) == Decimal(t['price']))
    method_ok = sum(1 for t, c in matched if c.pricing_method == t['method'])
    amounts_read = [a for a in (_pred_amount(c) for c in preds) if a is not None]
    numeric_ok = sum(1 for a in amounts_read if a in truth_amounts)
    unmatched_preds = [c for i, c in enumerate(preds) if i not in used]
    hallucinated = [c for c in unmatched_preds if c.is_selected and c.review_state != 'UNREADABLE']
    wrong_but_good = sum(1 for t, c in matched
                         if _pred_amount(c) != Decimal(t['price']) and c.review_state == 'LOOKS_GOOD')
    return {
        'id': truth['id'],
        'truth_rows': len(truth_rows),
        'pred_rows': len(preds),
        'matched': len(matched),
        'correct_pairs': correct_pairs,
        'method_ok': method_ok,
        'amounts_read': len(amounts_read),
        'amounts_correct': numeric_ok,
        'hallucinated': len(hallucinated),
        'hallucinated_names': [c.item_name for c in hallucinated][:5],
        'currency_ok': (currency or None) == truth['currency'],
        'wrong_marked_looks_good': wrong_but_good,
        'edits_needed': (len(truth_rows) - correct_pairs) + len(hallucinated)
        + sum(1 for t, c in matched if c.pricing_method != t['method'] and _pred_amount(c) == Decimal(t['price'])),
        'misses': [f"{t['name']}|{t['variant'] or ''}={t['price']}"
                   + ('' if c is None else f" got {c.item_name}={_pred_amount(c)}")
                   for t, c in pairs if c is None or _pred_amount(c) != Decimal(t['price'])][:6],
    }


def aggregate(results: list[dict]) -> dict:
    ok = [r for r in results if not r.get('error')]
    def s(key):
        return sum(r[key] for r in ok)
    truth = s('truth_rows') or 1
    matched = s('matched') or 1
    latencies = sorted(r['latency_ms'] for r in ok if r.get('latency_ms') is not None)
    def pct(p):
        if not latencies:
            return None
        return latencies[min(len(latencies) - 1, int(round(p * (len(latencies) - 1))))]
    return {
        'images': len(results),
        'images_failed': len(results) - len(ok),
        'truth_rows': s('truth_rows'),
        'service_recall': round(s('matched') / truth, 4),
        'pairing_accuracy_end_to_end': round(s('correct_pairs') / truth, 4),
        'pairing_accuracy_of_found': round(s('correct_pairs') / matched, 4),
        'numeric_price_accuracy': round(s('amounts_correct') / (s('amounts_read') or 1), 4),
        'pricing_method_accuracy': round(s('method_ok') / matched, 4),
        'currency_accuracy': round(sum(1 for r in ok if r['currency_ok']) / (len(ok) or 1), 4),
        'hallucinated_rows': s('hallucinated'),
        'hallucination_rate': round(s('hallucinated') / (s('pred_rows') or 1), 4),
        'wrong_price_marked_looks_good': s('wrong_marked_looks_good'),
        'avg_edits_per_import': round(s('edits_needed') / (len(ok) or 1), 2),
        'latency_p50_ms': pct(0.5),
        'latency_p90_ms': pct(0.9),
        'latency_mean_ms': round(statistics.mean(latencies)) if latencies else None,
    }
