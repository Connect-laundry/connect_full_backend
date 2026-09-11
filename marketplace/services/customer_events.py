"""Customer-facing notification events and copy.

Every entry here is allowed to create a durable Notification row and queue the
existing remote push pipeline. Internal technical events should not be added.
"""

from dataclasses import dataclass
from decimal import Decimal

from marketplace.models import Notification
from marketplace.services.notification_service import NotificationService


@dataclass(frozen=True)
class CustomerEventTemplate:
    title: str
    body: str
    category: str
    type: str = Notification.Type.SYSTEM
    priority: str = Notification.Priority.NORMAL
    action_url: str = ''


CUSTOMER_EVENT_TEMPLATES = {
    'SIGNUP_SUCCESS': CustomerEventTemplate(
        title='Welcome to Simame',
        body='Your account is ready. Laundry just got easier.',
        category='SIGNUP_SUCCESS',
        action_url='/notifications',
    ),
    'LOGIN_SUCCESS': CustomerEventTemplate(
        title='Welcome back',
        body="You're signed in to Simame.",
        category='LOGIN_SUCCESS',
        action_url='/notifications',
    ),
    'NEW_DEVICE_LOGIN': CustomerEventTemplate(
        title='New sign-in',
        body='Your Simame account was signed in on a new device.',
        category='NEW_DEVICE_LOGIN',
        priority=Notification.Priority.HIGH,
        action_url='/settings/account',
    ),
    'PASSWORD_CHANGED': CustomerEventTemplate(
        title='Password updated',
        body='Your Simame password was changed successfully.',
        category='PASSWORD_CHANGED',
        priority=Notification.Priority.HIGH,
        action_url='/settings/account',
    ),
    'PAYMENT_PENDING': CustomerEventTemplate(
        title='Payment pending',
        body='We are waiting for your payment provider to confirm this payment.',
        category='PAYMENT_PENDING',
        type=Notification.Type.ORDER,
    ),
    'PAYMENT_SUCCESS': CustomerEventTemplate(
        title='Payment received',
        body='We have received your payment.',
        category='PAYMENT_SUCCESS',
        type=Notification.Type.ORDER,
        priority=Notification.Priority.HIGH,
    ),
    'PAYMENT_FAILED': CustomerEventTemplate(
        title='Payment could not be completed',
        body='Your payment was not completed. You can try again in Simame.',
        category='PAYMENT_FAILED',
        type=Notification.Type.ORDER,
        priority=Notification.Priority.HIGH,
    ),
    'REFUND_INITIATED': CustomerEventTemplate(
        title='Refund started',
        body='Your refund request has started. We will update you when it settles.',
        category='REFUND_INITIATED',
        type=Notification.Type.ORDER,
        priority=Notification.Priority.HIGH,
    ),
    'REFUND_COMPLETED': CustomerEventTemplate(
        title='Refund processed',
        body='Your refund has been processed.',
        category='PAYMENT_REFUNDED',
        type=Notification.Type.ORDER,
        priority=Notification.Priority.HIGH,
    ),
    'ADDRESS_SAVED': CustomerEventTemplate(
        title='Address saved',
        body='Your address was saved successfully.',
        category='ADDRESS_SAVED',
        action_url='/settings/account',
    ),
    'ADDRESS_UPDATED': CustomerEventTemplate(
        title='Address updated',
        body='Your address was updated successfully.',
        category='ADDRESS_UPDATED',
        action_url='/settings/account',
    ),
    'ADDRESS_REMOVED': CustomerEventTemplate(
        title='Address removed',
        body='Your address was removed from Simame.',
        category='ADDRESS_REMOVED',
        action_url='/settings/account',
    ),
    'PROFILE_UPDATED': CustomerEventTemplate(
        title='Profile updated',
        body='Your Simame profile was updated successfully.',
        category='PROFILE_UPDATED',
        action_url='/settings/account',
    ),
}


def _money(value):
    if value in (None, ''):
        return ''
    amount = Decimal(str(value))
    return f'GHS {amount.quantize(Decimal("0.01"))}'


def notify_customer_event(user, event, *, dedup_key, related_order=None,
                          payment=None, amount=None, order_no='', body=None,
                          action_url=None, push=True):
    """Create a customer notification from a named product event."""
    template = CUSTOMER_EVENT_TEMPLATES[event]
    resolved_order = related_order or getattr(payment, 'order', None)
    resolved_order_no = order_no or getattr(resolved_order, 'order_no', '') or ''
    resolved_amount = amount if amount is not None else getattr(payment, 'amount', None)
    resolved_body = body or template.body

    if event in {'PAYMENT_SUCCESS', 'PAYMENT_PENDING'} and resolved_amount:
        suffix = f' for order {resolved_order_no}' if resolved_order_no else ''
        if event == 'PAYMENT_SUCCESS':
            resolved_body = f'We have received your payment of {_money(resolved_amount)}{suffix}.'
        else:
            resolved_body = f'We are waiting for your provider to confirm {_money(resolved_amount)}{suffix}.'
    elif event == 'PAYMENT_FAILED' and resolved_order_no:
        resolved_body = f'Your payment for order {resolved_order_no} was not completed. You can try again in Simame.'
    elif event == 'REFUND_COMPLETED' and resolved_amount:
        suffix = f' for order {resolved_order_no}' if resolved_order_no else ''
        resolved_body = f'Your refund of {_money(resolved_amount)}{suffix} has been processed.'

    return NotificationService.notify_user(
        user,
        title=template.title,
        body=resolved_body,
        type=template.type,
        category=template.category,
        priority=template.priority,
        action_url=action_url if action_url is not None else template.action_url,
        related_order=resolved_order,
        dedup_key=dedup_key,
        push=push,
    )
