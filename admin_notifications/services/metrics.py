"""Structured events and Sentry signals for admin notifications.

Only operational labels leave the process: event name, channel, provider,
event type, environment, error class, order number. Never a phone number,
address, message body or credential.
"""
import logging

from .. import conf

logger = logging.getLogger('admin_notifications')

CREATED = 'admin_notification.created'
SUBMITTED = 'admin_notification.submitted'
DELIVERED = 'admin_notification.delivered'
FAILED = 'admin_notification.failed'
RETRY = 'admin_notification.retry'
DEAD = 'admin_notification.dead'
UNKNOWN = 'admin_notification.unknown'
UNDELIVERED = 'admin_notification.undelivered'
EMIT_ERROR = 'admin_notification.emit_error'


def record(name: str, *, level=logging.INFO, channel='', provider='', event_type='', error_class='',
           order_no='', **extra):
    fields = {
        'event': name,
        'channel': channel,
        'provider': provider,
        'event_type': event_type,
        'environment': conf.environment(),
        'error_class': error_class,
        'order_no': order_no,
    }
    fields.update({k: v for k, v in extra.items() if isinstance(v, (int, float, bool, str)) or v is None})
    logger.log(level, name, extra=fields)


def alert(name: str, message: str, *, level='error', **tags):
    """Sentry message with operational tags only. A no-op if Sentry is off."""
    record(name, level=logging.ERROR if level in ('error', 'fatal') else logging.WARNING, **tags)
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            scope.set_tag('admin_notification.event', name)
            scope.set_tag('environment_label', conf.environment())
            for key in ('channel', 'provider', 'event_type', 'error_class'):
                if tags.get(key):
                    scope.set_tag(f'admin_notification.{key}', tags[key])
            if tags.get('order_no'):
                scope.set_extra('order_no', tags['order_no'])
            sentry_sdk.capture_message(message, level=level)
    except Exception:  # pragma: no cover - monitoring must never break sending
        pass
