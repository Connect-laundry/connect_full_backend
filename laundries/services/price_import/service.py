"""Price-list import job lifecycle: start, confirm, cancel.

Invariants:
* Extraction creates drafts only. Live pricing changes only in ``confirm``,
  which the owner triggers with their edited rows.
* ``confirm`` revalidates every submitted value server side, runs in one
  transaction, and is idempotent: a repeat confirm replays the stored outcome
  and cannot create duplicates.
* No provider call happens inside a DB transaction.
* An identical image re-uploaded by the *same laundry* reuses the earlier
  extraction instead of another paid call. Results are never shared across
  laundries.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from laundries.models.laundry import Laundry, OwnerAuditLog
from laundries.models.price_import import PriceListDraftItem, PriceListImportJob
from laundries.models.pricing import (
    BasketDefinition,
    LaundryPricingItem,
    LaundryWeightPricing,
    PricingCatalogVersion,
)

from . import telemetry
from .image import PreparedImage
from .matching import match_candidates, normalise_name
from .pipeline import ExtractionOutcome, run_extraction
from .documents import archive_pages
from .schema import DraftCandidate
from .validation import clean_text

logger = logging.getLogger(__name__)

Status = PriceListImportJob.Status
STALE_PROCESSING = timedelta(minutes=3)
MAX_CONFIRM_ROWS = 300
HARD_MAX_PRICE = Decimal('100000')


class ImportRefused(Exception):
    def __init__(self, code: str, message: str, http_status: int, job=None):
        super().__init__(message)
        self.code, self.message, self.http_status, self.job = code, message, http_status, job


# --- availability -----------------------------------------------------------

def ai_available_for(laundry: Laundry | None, user=None) -> tuple[bool, str]:
    if not settings.PRICE_LIST_AI_ENABLED:
        return False, 'DISABLED'
    allow = {a.lower() for a in settings.PRICE_LIST_AI_LAUNDRY_ALLOWLIST}
    if allow:
        # Canary allowlist: a laundry id, or (for owners still onboarding,
        # who have no laundry yet) the owner's user id or email.
        idents = set()
        if laundry is not None:
            idents.add(str(laundry.id).lower())
        if user is not None:
            idents.update({str(user.pk).lower(), (getattr(user, 'email', '') or '').lower()})
        if not idents & allow:
            return False, 'NOT_ENABLED_FOR_LAUNDRY'
    from .providers import get_provider
    names = [settings.PRICE_LIST_PRIMARY_PROVIDER, settings.PRICE_LIST_FALLBACK_PROVIDER]
    if not any((p := get_provider(n)) and p.is_configured() for n in names):
        return False, 'NOT_CONFIGURED'
    return True, ''


def imports_last_24h(laundry: Laundry | None, user=None) -> int:
    return _imports_last_24h(_scope(laundry, user))


def _imports_last_24h(scope) -> int:
    since = timezone.now() - timedelta(hours=24)
    return scope.filter(created_at__gte=since, served_from_cache=False).exclude(
        error_code='AI_TEMPORARILY_UNAVAILABLE').count()


def expire_stale(queryset=None) -> None:
    """A request killed mid-extraction leaves PROCESSING behind; retire it."""
    cutoff = timezone.now() - STALE_PROCESSING
    qs = queryset if queryset is not None else PriceListImportJob.objects.all()
    qs.filter(status=Status.PROCESSING, updated_at__lt=cutoff).update(
        status=Status.FAILED, error_code='AI_TEMPORARILY_UNAVAILABLE',
        error="This scan didn't finish. Please try again.", updated_at=timezone.now(),
    )


# --- start ------------------------------------------------------------------

@dataclass
class StartResult:
    job: PriceListImportJob
    created: bool
    deduplicated: bool = False


def _drafts_from_candidates(job, candidates: list[DraftCandidate]) -> list[PriceListDraftItem]:
    rows = []
    for position, c in enumerate(candidates):
        rows.append(PriceListDraftItem(
            job=job, position=position, item_name=c.item_name[:120], raw_name=c.raw_name[:200],
            variant=c.variant[:80], pricing_method=c.pricing_method, suggested_price=c.price,
            price_per_kg=c.price_per_kg, surcharge_type=c.surcharge_type[:40],
            surcharge_amount=c.surcharge_amount, category=c.category[:80],
            source_text=c.source_text[:300], confidence=c.confidence, review_state=c.review_state,
            warnings=c.warnings[:20], match_type=c.match_type, matched_item_id=c.matched_item_id,
            is_selected=c.is_selected,
            pricing_details=c.pricing_details,
        ))
    return rows


def owner_jobs(user):
    """Jobs this owner may see: their laundry's, plus their own pre-laundry
    (onboarding) scans. Ownership is always decided here, never by the client."""
    return PriceListImportJob.objects.filter(
        Q(laundry__owner=user) | Q(laundry__isnull=True, created_by=user)
    )


def _scope(laundry, user):
    """Jobs sharing dedup/limit accounting with this upload."""
    if laundry is not None:
        return PriceListImportJob.objects.filter(laundry=laundry)
    return PriceListImportJob.objects.filter(laundry__isnull=True, created_by=user)


def start_import(*, laundry: Laundry | None, user, image: PreparedImage, request=None,
                 background: bool = False) -> StartResult:
    """Create a job and extract.

    ``laundry`` may be None during onboarding (the laundry is only created at
    the wizard's final step): the scan then belongs to the owner and its
    drafts prefill the wizard, which saves through the normal pricing flow.

    ``background=True`` returns the PROCESSING job immediately and extracts
    in a thread, so no proxy/browser has to hold a request open for up to a
    minute. The caller polls the job. No Celery/Redis involved; a thread killed
    with its worker leaves a PROCESSING job that ``expire_stale`` retires.
    """
    existing_items = (LaundryPricingItem.objects.filter(laundry=laundry)
                      if laundry is not None else LaundryPricingItem.objects.none())
    scope = _scope(laundry, user)
    expire_stale(scope)

    with transaction.atomic():
        # Serialise concurrent uploads for this laundry/owner (row lock on Postgres).
        if laundry is not None:
            Laundry.objects.select_for_update().filter(pk=laundry.pk).first()
        else:
            type(user).objects.select_for_update().filter(pk=user.pk).first()
        prior = (
            scope.filter(image_sha256=image.sha256)
            .exclude(status=Status.FAILED).order_by('-created_at').first()
        )
        if prior is not None and prior.status in (Status.READY, Status.PROCESSING):
            telemetry.event('dedup_hit', mode='same_job')
            return StartResult(job=prior, created=False, deduplicated=True)

        if scope.filter(status=Status.PROCESSING).exists():
            raise ImportRefused('IMPORT_IN_PROGRESS',
                                'A price list is already being scanned. Please wait for it to finish.', 409)

        cached = prior is not None and prior.result.get('items')
        if not cached:
            if _imports_last_24h(scope) >= settings.PRICE_LIST_DAILY_LIMIT_PER_LAUNDRY:
                raise ImportRefused(
                    'DAILY_LIMIT_REACHED',
                    "You've reached today's limit for scanning price lists. "
                    'Please try again tomorrow or add your services manually.', 429,
                )
            busy = PriceListImportJob.objects.filter(
                status=Status.PROCESSING, updated_at__gte=timezone.now() - STALE_PROCESSING,
            ).count()
            if busy >= settings.PRICE_LIST_MAX_CONCURRENT:
                raise ImportRefused('SERVER_BUSY',
                                    'Our scanner is busy right now. Please try again in a minute.', 503)

        job = PriceListImportJob.objects.create(
            laundry=laundry, created_by=user, status=Status.PROCESSING,
            image_sha256=image.sha256, original_filename=image.original_filename,
        )

    if cached:
        return StartResult(job=_serve_from_cache(job, prior, existing_items), created=True, deduplicated=True)

    if background:
        worker = threading.Thread(
            target=_extract_in_background, args=(job.pk, image), daemon=True,
            name=f'price-import-{job.pk}',
        )
        worker.start()
        return StartResult(job=job, created=True)

    _extract_into(job, image, list(existing_items), request=request)
    return StartResult(job=job, created=True)


def _extract_in_background(job_id, image: PreparedImage) -> None:
    from django.db import connection
    try:
        job = PriceListImportJob.objects.select_related('laundry').get(pk=job_id)
        existing = (list(LaundryPricingItem.objects.filter(laundry=job.laundry))
                    if job.laundry_id else [])
        _extract_into(job, image, existing)
    except Exception:
        logger.exception('price_import background extraction crashed', extra={'job_id': str(job_id)})
        PriceListImportJob.objects.filter(pk=job_id, status=Status.PROCESSING).update(
            status=Status.FAILED, error_code='AI_TEMPORARILY_UNAVAILABLE',
            error="This scan didn't finish. Please try again.", updated_at=timezone.now(),
        )
    finally:
        connection.close()


def _extract_into(job, image: PreparedImage, existing_items, request=None) -> None:
    outcome = _extract_document(image, existing_items)

    # Store only sanitized page pixels in a private archive. This is optional:
    # storage failure cannot discard an otherwise useful editable draft.
    try:
        name, content, count = archive_pages(image)
        job.source_document.save(name, ContentFile(content), save=False)
        job.source_pages = count
    except Exception:
        logger.warning('price_import private source storage unavailable', extra={'job_id': str(job.pk)})

    with transaction.atomic():
        job.provider = outcome.provider or 'none'
        job.model_name = outcome.model[:60]
        job.provider_trace = outcome.trace
        job.latency_ms = outcome.latency_ms
        job.document_warnings = outcome.document_warnings[:30]
        job.currency = outcome.currency
        job.completed_at = timezone.now()
        if outcome.ok:
            PriceListDraftItem.objects.bulk_create(_drafts_from_candidates(job, outcome.candidates))
            job.status = Status.READY
            job.result = {
                'currency': outcome.currency,
                'document_warnings': outcome.document_warnings,
                'items': [c.to_json() for c in outcome.candidates],
            }
        else:
            job.status = Status.FAILED
            job.error_code = outcome.error_code
            job.error = outcome.error_message[:255]
        job.save()


def _extract_document(image, existing_items):
    if not image.pages:
        return run_extraction(image, existing_items)
    started = time.monotonic()
    deadline = started + settings.PRICE_LIST_TOTAL_BUDGET_SECONDS
    combined = ExtractionOutcome(ok=False, trace={'pages': []})
    for index, page in enumerate(image.pages, start=1):
        remaining = deadline - time.monotonic()
        if remaining < 5:
            combined.document_warnings.append(f'PAGE_{index}_NOT_READ')
            continue
        budget = remaining / (len(image.pages) - index + 1)
        outcome = run_extraction(page, existing_items, budget_seconds=budget)
        combined.trace['pages'].append({'page': index, **outcome.trace})
        combined.document_warnings.extend(outcome.document_warnings)
        if not outcome.ok:
            combined.document_warnings.append(f'PAGE_{index}_NOT_READ')
            continue
        combined.ok = True
        combined.provider, combined.model = outcome.provider, outcome.model
        if outcome.currency == 'GHS':
            combined.currency = 'GHS'
        for candidate in outcome.candidates:
            candidate.pricing_details['source_page'] = index
            candidate.pricing_details['source_reference'] = f'Page {index}'
        combined.candidates.extend(outcome.candidates)
    combined.document_warnings = list(dict.fromkeys(combined.document_warnings))
    # Conflicts across pages deserve the same review as conflicts on one page.
    from .validation import _dedupe, assign_review_state
    _dedupe(combined.candidates)
    for candidate in combined.candidates:
        assign_review_state(candidate, currency_confirmed=bool(combined.currency), trusted_source=True)
    combined.latency_ms = int((time.monotonic() - started) * 1000)
    if not combined.ok:
        combined.error_code = 'NO_PRICES_FOUND'
        combined.error_message = 'No pages could be read. Try clearer photos or enter your prices manually.'
    return combined


def _serve_from_cache(job, prior, existing_items) -> PriceListImportJob:
    candidates = [DraftCandidate.from_json(d) for d in prior.result.get('items', [])]
    for c in candidates:                    # re-match against today's catalogue
        c.match_type, c.matched_item_id = 'NONE', None
        c.warnings = [w for w in c.warnings if w not in ('EXISTING_ITEM_MATCH', 'POSSIBLE_MATCH')]
    match_candidates(candidates, existing_items)
    with transaction.atomic():
        PriceListDraftItem.objects.bulk_create(_drafts_from_candidates(job, candidates))
        job.status = Status.READY
        job.served_from_cache = True
        job.provider, job.model_name = prior.provider, prior.model_name
        job.currency = prior.currency
        job.document_warnings = prior.document_warnings
        job.result = prior.result
        job.provider_trace = {'cache_hit_of': str(prior.id)}
        # Independent archive reference; retention can remove either job's file.
        if prior.source_document:
            try:
                import uuid
                with prior.source_document.open('rb') as source:
                    job.source_document.save(f'{uuid.uuid4().hex}.zip', ContentFile(source.read()), save=False)
                job.source_pages = prior.source_pages
            except Exception:
                logger.warning('price_import cached source unavailable', extra={'job_id': str(job.pk)})
        job.latency_ms = 0
        job.completed_at = timezone.now()
        job.save()
    telemetry.event('dedup_hit', mode='cached_result')
    return job


# --- confirm ----------------------------------------------------------------

class ConfirmRejected(Exception):
    def __init__(self, code: str, message: str, errors: list | None = None, http_status: int = 400):
        super().__init__(message)
        self.code, self.message, self.errors, self.http_status = code, message, errors or [], http_status


def _decimal(value, field: str, index: int, errors: list) -> Decimal | None:
    if value in (None, ''):
        errors.append({'row': index, 'field': field, 'message': 'A price is required.'})
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        errors.append({'row': index, 'field': field, 'message': 'Enter a valid amount, e.g. 15.00.'})
        return None
    if not d.is_finite() or d < 0:
        errors.append({'row': index, 'field': field, 'message': 'Price cannot be negative.'})
        return None
    if d > HARD_MAX_PRICE:
        errors.append({'row': index, 'field': field, 'message': 'This price is too large.'})
        return None
    if d != d.quantize(Decimal('0.01')):
        errors.append({'row': index, 'field': field, 'message': 'Use at most 2 decimal places.'})
        return None
    return d.quantize(Decimal('0.01'))


def _normalise_rows(rows: list[dict], laundry, drafts: dict) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    clean: list[dict] = []
    for index, row in enumerate(rows):
        legacy = 'action' not in row and 'pricing_method' not in row and 'pricing_basis' not in row
        action = (row.get('action') or 'CREATE').upper()
        if action not in ('CREATE', 'UPDATE', 'IGNORE'):
            errors.append({'row': index, 'field': 'action', 'message': 'Choose create, update or ignore.'})
            continue
        draft_id = str(row.get('draft_id') or '') or None
        if draft_id and draft_id not in drafts:
            errors.append({'row': index, 'field': 'draft_id', 'message': 'Unknown draft row.'})
            continue
        if action == 'IGNORE':
            clean.append({'action': 'IGNORE', 'draft_id': draft_id})
            continue
        method = 'PER_ITEM' if legacy else (row.get('pricing_basis') or row.get('pricing_method') or '').upper()
        if method not in ('PER_ITEM', 'PER_KG', 'PER_BASKET', 'WEIGHT_TIER', 'FIXED_PACKAGE'):
            errors.append({'row': index, 'field': 'pricing_method',
                           'message': 'Choose how this service is priced.'})
            continue
        entry = {'action': action, 'draft_id': draft_id, 'pricing_method': method, 'row': index}
        unified = 'pricing_basis' in row or method in ('PER_BASKET', 'WEIGHT_TIER', 'FIXED_PACKAGE')
        entry['unified'] = unified
        if method != 'PER_KG' or unified:
            name = clean_text(row.get('item_name'), 120)
            if not name:
                errors.append({'row': index, 'field': 'item_name', 'message': 'Enter a service name.'})
                continue
            entry['item_name'] = name
            entry['category'] = clean_text(row.get('category'), 80)
            entry['unit_price'] = _decimal(row.get('unit_price'), 'unit_price', index, errors)
            if entry['unit_price'] is None:
                continue
            if action == 'UPDATE':
                target_id = row.get('existing_item_id')
                target = None
                if target_id:
                    try:
                        # Scoped to this laundry: a foreign id simply isn't found.
                        target = LaundryPricingItem.objects.filter(laundry=laundry, id=target_id).first()
                    except Exception:
                        target = None
                if target is None:
                    errors.append({'row': index, 'field': 'existing_item_id',
                                   'message': 'Choose which of your existing services to update.'})
                    continue
                entry['target'] = target
                if not unified and target.pricing_basis != 'PER_ITEM':
                    errors.append({'row': index, 'field': 'pricing_method', 'message': 'Review this service using its current pricing method before updating it.'})
                    continue
            if unified:
                if not getattr(settings, 'UNIFIED_PRICING_ENABLED', False):
                    errors.append({'row': index, 'field': 'pricing_method', 'message': 'These pricing options are not available yet. Your draft is saved.'})
                    continue
                entry.update({
                    key: row[key]
                    for key in ('service_type', 'pricing_rules', 'basket', 'basket_definition', 'description')
                    if key in row
                })
                entry['basket_id'] = row.get('basket')
        else:
            entry['price_per_kg'] = _decimal(
                row.get('price_per_kg', row.get('unit_price')), 'price_per_kg', index, errors,
            )
            if entry['price_per_kg'] is None:
                continue
        clean.append(entry)

    per_kg = [r for r in clean if r.get('pricing_method') == 'PER_KG' and not r.get('unified')]
    if len(per_kg) > 1:
        errors.append({'row': None, 'field': 'pricing_method',
                       'message': 'Your laundry can have one per-kg price. Keep one per-kg row and ignore the others.'})
    creates = [normalise_name(r['item_name']) for r in clean
               if r.get('action') == 'CREATE' and 'item_name' in r]
    dupes = {n for n in creates if creates.count(n) > 1}
    if dupes:
        errors.append({'row': None, 'field': 'item_name',
                       'message': 'Some services appear more than once. Rename or remove the duplicates.'})
    targets = [r['target'].id for r in clean if 'target' in r]
    if len(targets) != len(set(targets)):
        errors.append({'row': None, 'field': 'existing_item_id',
                       'message': 'Two rows update the same existing service.'})
    return clean, errors


def _edit_stats(clean: list[dict], drafts: dict) -> dict:
    submitted = {r['draft_id'] for r in clean if r.get('draft_id')}
    edited = 0
    for r in clean:
        d = drafts.get(r.get('draft_id'))
        if d is None or r['action'] == 'IGNORE':
            continue
        if r['pricing_method'] != d.pricing_method:
            edited += 1
        elif r['pricing_method'] == 'PER_ITEM' and (r['item_name'] != d.item_name or r['unit_price'] != d.suggested_price):
            edited += 1
        elif r['pricing_method'] == 'PER_KG' and r.get('price_per_kg', r.get('unit_price')) != d.price_per_kg:
            edited += 1
    return {
        'drafts': len(drafts),
        'edited': edited,
        'added': sum(1 for r in clean if not r.get('draft_id') and r['action'] != 'IGNORE'),
        'ignored': sum(1 for r in clean if r['action'] == 'IGNORE') + len(set(drafts) - submitted),
    }


def _basket_for_import(*, laundry, entry: dict, draft) -> BasketDefinition | None:
    """Resolve an explicitly selected basket or publish the draft's basket metadata.

    Draft extraction is intentionally advisory, so this helper only creates a
    definition when both a name and capacity were actually extracted. Reusing
    an existing definition keeps repeated confirmations idempotent and avoids
    duplicate baskets for multiple imported services.
    """
    basket_id = entry.get('basket_id')
    if basket_id:
        return BasketDefinition.objects.filter(
            id=basket_id, laundry=laundry, is_active=True,
        ).first()

    details = (draft.pricing_details if draft is not None else {}) or {}
    explicit = entry.get('basket_definition')
    if explicit is not None:
        from laundries.serializers.pricing import BasketDefinitionSerializer
        validator = BasketDefinitionSerializer(data=explicit)
        if not validator.is_valid():
            raise ConfirmRejected('VALIDATION_FAILED', 'Check the basket details.', [
                {'row': entry['row'], 'field': 'basket_definition', 'message': str(validator.errors)},
            ])
        basket_data = dict(validator.validated_data)
    else:
        basket_data = {
            'name': clean_text(details.get('basket_name'), 120),
            'capacity_description': clean_text(details.get('basket_capacity'), 500),
            'inclusions': list(details.get('inclusions') or []),
            'exclusions': list(details.get('restrictions') or []),
            'overfill_policy': 'Any extra charge requires your approval before payment.',
        }
    name = basket_data['name']
    capacity = basket_data['capacity_description']
    if not name or not capacity:
        return None

    basket = BasketDefinition.objects.filter(
        laundry=laundry, name__iexact=name,
    ).first()
    if basket is not None:
        if not basket.is_active or any(getattr(basket, key) != value for key, value in basket_data.items()):
            raise ConfirmRejected('BASKET_CONFLICT', 'A basket with this name has different details. Select your existing basket or use a new name.', http_status=409)
        return basket

    return BasketDefinition.objects.create(
        laundry=laundry,
        **basket_data,
    )


def _validate_unified_import_entry(entry: dict, laundry) -> list[dict]:
    """Validate a unified import entry after its basket has been resolved."""
    from types import SimpleNamespace
    from laundries.serializers.pricing import LaundryPricingItemSerializer

    data = {
        key: entry[key]
        for key in ('service_type', 'pricing_rules', 'basket', 'description')
        if key in entry
    }
    data.update(
        item_name=entry['item_name'],
        category=entry['category'],
        unit_price=str(entry['unit_price']),
        pricing_basis=entry['pricing_method'],
    )
    validator = LaundryPricingItemSerializer(
        instance=entry.get('target'),
        data=data,
        partial=entry['action'] == 'UPDATE',
        context={'request': SimpleNamespace(user=laundry.owner)},
    )
    if not validator.is_valid():
        return [{'row': entry['row'], 'field': 'pricing_rules', 'message': str(validator.errors)}]
    entry['serializer'] = validator
    return []


def confirm_import(*, job_id, owner, rows: list[dict], currency_confirmed: bool | None) -> tuple[dict, bool]:
    """Returns (result, replayed)."""
    if len(rows) > MAX_CONFIRM_ROWS:
        raise ConfirmRejected('TOO_MANY_ROWS', f'You can import at most {MAX_CONFIRM_ROWS} services at once.')
    try:
        with transaction.atomic():
            job = owner_jobs(owner).select_for_update(of=('self',)).filter(id=job_id).first()
            if job is None:
                raise ConfirmRejected('NOT_FOUND', 'Import job not found.', http_status=404)
            if job.laundry_id is None and job.status == Status.READY:
                # An onboarding scan: attach it once the owner's laundry exists.
                own = Laundry.objects.filter(owner=owner).first()
                if own is None:
                    raise ConfirmRejected('NO_LAUNDRY', 'Register a laundry before importing a price list.',
                                          http_status=409)
                job.laundry = own
                job.save(update_fields=['laundry', 'updated_at'])
            if job.status == Status.CONFIRMED:
                return job.confirm_result or {'created': [], 'skipped': [], 'updated': []}, True
            if job.status != Status.READY:
                raise ConfirmRejected('NOT_CONFIRMABLE',
                                      'This import cannot be confirmed. Please start a new scan.', http_status=409)
            legacy = all('action' not in r and 'pricing_method' not in r and 'pricing_basis' not in r for r in rows)
            unsure_currency = job.currency != 'GHS' or bool({'FOREIGN_CURRENCY', 'CURRENCY_UNCONFIRMED'} & set(job.document_warnings or []))
            if unsure_currency and not legacy and not currency_confirmed:
                raise ConfirmRejected('CURRENCY_CONFIRMATION_REQUIRED',
                                      'Please confirm that these prices are in Ghana cedis (GHS).')
            laundry = job.laundry
            # All catalog writers serialize on the parent, including imports.
            Laundry.objects.select_for_update().get(pk=laundry.pk)
            drafts = {str(d.id): d for d in job.draft_items.all()}
            clean, errors = _normalise_rows(rows, laundry, drafts)
            if errors:
                raise ConfirmRejected('VALIDATION_FAILED', 'Some rows need attention before importing.', errors)
            for entry in clean:
                if not entry.get('unified') or entry['action'] == 'IGNORE':
                    continue
                if entry['pricing_method'] == 'PER_BASKET':
                    basket = _basket_for_import(
                        laundry=laundry,
                        entry=entry,
                        draft=drafts.get(entry.get('draft_id')),
                    )
                    if basket is None:
                        errors.append({
                            'row': entry['row'],
                            'field': 'basket',
                            'message': (
                                'Select an active basket or provide a readable basket name '
                                'and capacity before importing this service.'
                            ),
                        })
                    else:
                        entry['basket'] = basket.id
                        entry['basket_id'] = basket.id
                errors.extend(_validate_unified_import_entry(entry, laundry))
            if errors:
                raise ConfirmRejected('VALIDATION_FAILED', 'Some rows need attention before importing.', errors)
            active = [r for r in clean if r['action'] != 'IGNORE']
            if not active:
                raise ConfirmRejected('NOTHING_TO_IMPORT', 'Select at least one service to import.')

            existing = {normalise_name(i.item_name): i for i in LaundryPricingItem.objects.filter(laundry=laundry)}
            weight = LaundryWeightPricing.objects.filter(laundry=laundry).first()
            if any(r['pricing_method'] == 'PER_KG' and not r.get('unified') and r['action'] == 'CREATE' for r in active) and weight:
                raise ConfirmRejected('VALIDATION_FAILED', 'Some rows need attention before importing.', [{
                    'row': None, 'field': 'price_per_kg',
                    'message': f'You already have a per-kg price (GHS {weight.base_price_per_kg}). '
                               'Choose "Update" to replace it, or ignore this row.',
                }])

            # Snapshot the catalogue before changing it (same as CSV import).
            snapshot = [
                {'item_name': i.item_name, 'category': i.category, 'unit_price': str(i.unit_price),
                 'is_active': i.is_active, 'display_order': i.display_order,
                 'service_type': i.service_type, 'pricing_basis': i.pricing_basis,
                 'pricing_rules': i.pricing_rules, 'basket': str(i.basket_id) if i.basket_id else None,
                 'description': i.description, 'currency': i.currency, 'revision': i.revision}
                for i in LaundryPricingItem.objects.filter(laundry=laundry).order_by('display_order', 'item_name')
            ]
            last = PricingCatalogVersion.objects.filter(laundry=laundry).order_by('-version_number').first()
            PricingCatalogVersion.objects.create(
                laundry=laundry, version_number=(last.version_number + 1) if last else 1, items_data=snapshot,
            )

            created, skipped, updated = [], [], []
            next_order = LaundryPricingItem.objects.filter(laundry=laundry).count()
            for r in active:
                if r.get('unified'):
                    key = normalise_name(r['item_name'])
                    if r['action'] == 'CREATE' and key in existing:
                        skipped.append(r['item_name'])
                        continue
                    item = r['serializer'].save(laundry=laundry, **({'display_order': next_order} if r['action'] == 'CREATE' else {}))
                    existing[key] = item
                    (created if r['action'] == 'CREATE' else updated).append(item.item_name)
                    next_order += r['action'] == 'CREATE'
                    continue
                if r['pricing_method'] == 'PER_KG':
                    if weight is None:
                        weight = LaundryWeightPricing.objects.create(
                            laundry=laundry, base_price_per_kg=r['price_per_kg'])
                        created.append('Per-kg price')
                    else:
                        weight.base_price_per_kg = r['price_per_kg']
                        weight.save(update_fields=['base_price_per_kg', 'updated_at'])
                        updated.append('Per-kg price')
                    continue
                if r['action'] == 'UPDATE':
                    target = r['target']
                    target.unit_price = r['unit_price']
                    if r['category']:
                        target.category = r['category']
                    target.save(update_fields=['unit_price', 'category', 'updated_at'])
                    updated.append(target.item_name)
                    continue
                key = normalise_name(r['item_name'])
                if key in existing:
                    skipped.append(r['item_name'])     # never overwrite on CREATE
                    continue
                item = LaundryPricingItem.objects.create(
                    laundry=laundry, item_name=r['item_name'], unit_price=r['unit_price'],
                    category=r['category'], display_order=next_order,
                )
                existing[key] = item
                created.append(item.item_name)
                next_order += 1

            stats = _edit_stats(clean, drafts)
            result = {'created': created, 'skipped': skipped, 'updated': updated, 'edits': stats}
            OwnerAuditLog.objects.create(
                laundry=laundry, actor=owner, action='PRICE_LIST_AI_IMPORT',
                details={'job_id': str(job.id), 'provider': job.provider, 'model': job.model_name,
                         'created_count': len(created), 'updated_count': len(updated),
                         'skipped_count': len(skipped), 'edits': stats},
            )
            job.status = Status.CONFIRMED
            job.confirmed_at = timezone.now()
            job.confirm_result = result
            job.save(update_fields=['status', 'confirmed_at', 'confirm_result', 'updated_at'])
    except IntegrityError:
        # A concurrent edit created the same name between our read and write.
        raise ConfirmRejected('CONFLICT', 'Your services changed while importing. Please review and try again.',
                              http_status=409)
    telemetry.event('confirmed', provider=job.provider, created=len(created), updated=len(updated),
                    skipped=len(skipped), **stats)
    return result, False


def cancel_import(*, job_id, owner) -> PriceListImportJob:
    with transaction.atomic():
        job = owner_jobs(owner).select_for_update(of=('self',)).filter(id=job_id).first()
        if job is None:
            raise ConfirmRejected('NOT_FOUND', 'Import job not found.', http_status=404)
        if job.status in (Status.READY, Status.FAILED):
            job.status = Status.CANCELLED
            job.save(update_fields=['status', 'updated_at'])
        elif job.status != Status.CANCELLED:
            raise ConfirmRejected('NOT_CANCELLABLE', 'This import can no longer be cancelled.', http_status=409)
    return job
