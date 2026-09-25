"""Usage and quality metrics for AI price-list import, from the database.

    python manage.py price_import_stats [--days 30]

Reports imports requested, provider calls/success/429/5xx, fallback count,
cache hits, p50/p90 latency, confirmation rate and average owner edits.
No Redis or metrics backend required: it reads provider traces off the jobs.
"""
import json
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from laundries.models.price_import import PriceListImportJob


def _pct(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


class Command(BaseCommand):
    help = 'AI price-list import usage/quality metrics.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30)

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(days=opts['days'])
        jobs = list(PriceListImportJob.objects.filter(created_at__gte=since))
        calls, outcomes = Counter(), Counter()
        for job in jobs:
            for attempt in (job.provider_trace or {}).get('attempts', []):
                provider = attempt.get('provider')
                calls[provider] += 1
                if attempt.get('ok'):
                    outcomes[f'{provider}_success'] += 1
                else:
                    outcomes[f'{provider}_{attempt.get("kind")}'] += 1
                    status = attempt.get('status')
                    if status == 429:
                        outcomes[f'{provider}_429'] += 1
                    elif status and status >= 500:
                        outcomes[f'{provider}_5xx'] += 1
        latencies = [j.latency_ms for j in jobs if j.latency_ms and not j.served_from_cache]
        confirmed = [j for j in jobs if j.status == PriceListImportJob.Status.CONFIRMED]
        extracted = [j for j in jobs if j.status in (PriceListImportJob.Status.READY,
                                                     PriceListImportJob.Status.CONFIRMED,
                                                     PriceListImportJob.Status.CANCELLED)]
        edits = [j.confirm_result.get('edits', {}) for j in confirmed if j.confirm_result]
        report = {
            'window_days': opts['days'],
            'imports_requested': len(jobs),
            'by_status': dict(Counter(j.status for j in jobs)),
            'failure_codes': dict(Counter(j.error_code for j in jobs if j.error_code)),
            'provider_calls': dict(calls),
            'provider_outcomes': dict(outcomes),
            'fallback_count': sum(1 for j in jobs if (j.provider_trace or {}).get('fallback_used')),
            'crosschecks': sum(1 for j in jobs if isinstance((j.provider_trace or {}).get('crosscheck'), dict)),
            'disagreements': sum(((j.provider_trace or {}).get('crosscheck') or {}).get('disagreed', 0)
                                 for j in jobs if isinstance((j.provider_trace or {}).get('crosscheck'), dict)),
            'duplicate_cache_hits': sum(1 for j in jobs if j.served_from_cache),
            'latency_p50_ms': _pct(latencies, 0.5),
            'latency_p90_ms': _pct(latencies, 0.9),
            'models': dict(Counter(j.model_name for j in jobs if j.model_name)),
            'confirmation_rate': round(len(confirmed) / len(extracted), 3) if extracted else None,
            'avg_edits_per_import': round(sum(e.get('edited', 0) + e.get('added', 0) for e in edits) / len(edits), 2)
            if edits else None,
            'tokens': sum(a.get('total_tokens') or 0 for j in jobs
                          for a in (j.provider_trace or {}).get('attempts', [])),
        }
        self.stdout.write(json.dumps(report, indent=1))
