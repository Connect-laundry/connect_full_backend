"""Owner-facing payout notifications.

These messages are intentionally plain. Owners should never see recipient codes,
webhook language, HTTP errors, or payment-provider internals.
"""

import logging

logger = logging.getLogger(__name__)


def _provider_label(laundry):
    provider = (getattr(laundry, 'payout_provider', '') or '').upper()
    if provider == 'MTN':
        return 'MTN Mobile Money'
    if provider == 'VOD':
        return 'Telecel Cash'
    if provider == 'ATL':
        return 'ATMoney'
    return provider or 'Mobile Money'


def _ending(laundry):
    raw = ''.join(ch for ch in str(
        getattr(laundry, 'payout_phone_normalized', '')
        or getattr(laundry, 'payout_phone', '')
        or getattr(laundry, 'phone_number', '')
    ) if ch.isdigit())
    return raw[-4:] if len(raw) >= 4 else ''


def _money(amount):
    return f"GHS {amount}"


def _notify(laundry, *, title, body, category, dedup_key, priority='NORMAL'):
    owner = getattr(laundry, 'owner', None)
    if owner is None:
        return None
    try:
        from marketplace.models import Notification
        from marketplace.services.notification_service import NotificationService

        return NotificationService.notify_user(
            owner,
            title=title,
            body=body,
            type=Notification.Type.SYSTEM,
            category=category,
            priority=getattr(Notification.Priority, priority, Notification.Priority.NORMAL),
            action_url='/earnings',
            dedup_key=dedup_key,
        )
    except Exception as exc:  # pragma: no cover - notifications must never block money state.
        logger.warning(
            'Owner payout notification failed',
            extra={
                'laundry_id': str(getattr(laundry, 'id', '')),
                'category': category,
                'error': str(exc),
            },
        )
        return None


def payout_account_ready(laundry, *, changed=False):
    ending = _ending(laundry)
    destination = f"{_provider_label(laundry)} ending {ending}" if ending else _provider_label(laundry)
    return _notify(
        laundry,
        title='Payout account ready' if not changed else 'Payout account updated',
        body=(
            f'Your future Simame earnings will be sent to {destination}.'
            if changed else
            f'Your Simame earnings will be sent to {destination} after completed orders.'
        ),
        category='PAYMENT',
        dedup_key=f"payout_account:{getattr(laundry, 'id', '')}:{getattr(laundry, 'recipient_created_at', '')}:{'changed' if changed else 'ready'}",
    )


def payout_account_needs_attention(laundry):
    return _notify(
        laundry,
        title='Check your payout account',
        body='We could not confirm your payout account. Your earnings are safe. Please try again or use another Mobile Money number.',
        category='PAYMENT',
        dedup_key=f"payout_account_failed:{getattr(laundry, 'id', '')}:{getattr(laundry, 'updated_at', '')}",
        priority='HIGH',
    )


def earnings_available(settlement):
    laundry = getattr(settlement, 'laundry', None)
    if laundry is None:
        return None
    return _notify(
        laundry,
        title='Earnings available',
        body=f'{_money(settlement.net_payable)} is now available from order {settlement.order.order_no}. Simame will send it automatically.',
        category='PAYMENT',
        dedup_key=f'earnings_available:{settlement.id}',
    )


def payout_started(payout):
    laundry = getattr(payout, 'laundry', None)
    if laundry is None:
        return None
    return _notify(
        laundry,
        title='Payout processing',
        body=f'{_money(payout.amount)} is on the way to your {_provider_label(laundry)} account.',
        category='PAYMENT',
        dedup_key=f'payout_started:{payout.id}',
    )


def payout_paid(payout):
    laundry = getattr(payout, 'laundry', None)
    if laundry is None:
        return None
    ending = _ending(laundry)
    destination = f'{_provider_label(laundry)} account ending {ending}' if ending else f'{_provider_label(laundry)} account'
    return _notify(
        laundry,
        title='Payout paid',
        body=f'{_money(payout.amount)} has been sent to your {destination}.',
        category='PAYMENT_SUCCESS',
        dedup_key=f'payout_paid:{payout.id}:{payout.paid_at}',
    )


def payout_needs_attention(payout):
    laundry = getattr(payout, 'laundry', None)
    if laundry is None:
        return None
    return _notify(
        laundry,
        title='Payout needs attention',
        body="We couldn't complete this payout. Your money is safe and we'll retry or contact you.",
        category='PAYMENT_FAILED',
        dedup_key=f'payout_attention:{payout.id}:{payout.updated_at}',
        priority='HIGH',
    )