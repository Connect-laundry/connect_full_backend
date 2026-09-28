"""Configuration validation shared by the Django system check and
``manage.py check_admin_notifications``. Never prints a secret: only whether
it is present and a short fingerprint.

System checks report Warnings rather than Errors on purpose: ``migrate`` runs
checks during deploy, and a typo in an alerting variable must not block an
unrelated release. Invalid recipients are never sent to either way.
"""
from __future__ import annotations

from urllib.parse import urlparse

from django.core import checks

from . import conf
from .providers.arkesel_sms import ArkeselSmsProvider
from .providers.arkesel_whatsapp import ArkeselWhatsAppProvider

OK, WARN, ERROR = 'OK', 'WARN', 'ERROR'


def validate_configuration() -> list[tuple[str, str]]:
    """Return [(level, message)] describing the current configuration."""
    report: list[tuple[str, str]] = []
    add = lambda level, msg: report.append((level, msg))  # noqa: E731

    enabled = conf.notifications_enabled()
    add(OK, f'ADMIN_ORDER_NOTIFICATIONS_ENABLED={enabled}')
    add(OK, f'ADMIN_NOTIFICATION_ENVIRONMENT={conf.environment()}')
    if not enabled:
        add(OK, 'Notifications are off: no events are recorded and nothing is sent.')
        return report

    from django.conf import settings
    if conf.is_production() and getattr(settings, 'DEBUG', False):
        add(WARN, 'Environment is production but DEBUG is on.')

    explicit_admin = str(getattr(settings, 'ADMIN_NOTIFICATION_ADMIN_BASE_URL', '') or '').strip().rstrip('/')
    if conf.is_production():
        problem = conf.url_problem(explicit_admin)
        if problem:
            add(ERROR, f'ADMIN_NOTIFICATION_ADMIN_BASE_URL {problem}. Production has no fallback: set it to '
                       'the https production backend origin.')
        else:
            add(OK, f'Admin order links use {explicit_admin}/admin/ordering/order/<id>/change/ (staff login required)')
    else:
        add(OK, f'Admin order links use {conf.admin_base_url() or "(relative path)"}')

    # SMS
    sms = conf.sms_enabled()
    add(OK, f'ADMIN_SMS_NOTIFICATIONS_ENABLED={sms}')
    if sms:
        parsed = conf.parse_recipients(getattr(settings, 'ADMIN_SMS_RECIPIENTS', ''))
        for bad in parsed.invalid:
            add(ERROR, f'ADMIN_SMS_RECIPIENTS has an invalid Ghana number ending ...{bad[-3:]}')
        if not parsed.valid:
            add(ERROR, 'SMS is enabled but ADMIN_SMS_RECIPIENTS has no valid number.')
        else:
            add(OK, f'SMS recipients: {len(parsed.valid)}')
        for missing in ArkeselSmsProvider().missing_config():
            add(ERROR, f'SMS is enabled but {missing} is not set/valid.')
        add(OK, f'ARKESEL_API_KEY {conf.fingerprint(conf.arkesel_api_key())}; sender "{conf.arkesel_sender_id()}"')
        if conf.is_production() and conf.arkesel_sms_sandbox():
            add(ERROR, 'ARKESEL_SMS_SANDBOX is on in production: Arkesel will not deliver any SMS.')
        elif conf.arkesel_sms_sandbox():
            add(OK, 'ARKESEL_SMS_SANDBOX=true (messages are accepted but not delivered).')
        callback = conf.arkesel_callback_url()
        explicit_cb = str(getattr(settings, 'ARKESEL_SMS_CALLBACK_BASE_URL', '') or '').strip().rstrip('/')
        if conf.is_production() and conf.url_problem(explicit_cb):
            add(ERROR, f'ARKESEL_SMS_CALLBACK_BASE_URL {conf.url_problem(explicit_cb)}: no SMS delivery reports.')
        elif not callback:
            add(WARN, 'ARKESEL_SMS_CALLBACK_SECRET / ARKESEL_SMS_CALLBACK_BASE_URL not set: no SMS delivery reports.')
        elif urlparse(callback).scheme != 'https':
            add(ERROR, 'SMS callback URL must be https.')
        elif len(conf.arkesel_callback_secret()) < 24:
            add(ERROR, 'ARKESEL_SMS_CALLBACK_SECRET must be at least 24 random characters.')
        else:
            add(OK, f'SMS delivery callback over https, secret {conf.fingerprint(conf.arkesel_callback_secret())}')
        add(OK, f'ADMIN_SMS_DETAIL_MODE={conf.sms_detail_mode()}; split above {conf.sms_max_chars()} chars')

    # WhatsApp
    whatsapp = conf.whatsapp_enabled()
    add(OK, f'ADMIN_WHATSAPP_NOTIFICATIONS_ENABLED={whatsapp}')
    if whatsapp:
        parsed = conf.parse_recipients(getattr(settings, 'ADMIN_WHATSAPP_RECIPIENTS', ''))
        for bad in parsed.invalid:
            add(ERROR, f'ADMIN_WHATSAPP_RECIPIENTS has an invalid Ghana number ending ...{bad[-3:]}')
        if not parsed.valid:
            add(ERROR, 'WhatsApp is enabled but ADMIN_WHATSAPP_RECIPIENTS has no valid number.')
        else:
            add(OK, f'WhatsApp recipients: {len(parsed.valid)}')
        for missing in ArkeselWhatsAppProvider().missing_config():
            add(ERROR, f'WhatsApp is enabled but {missing} is not set.')
        add(OK, f'ARKESEL_WHATSAPP_API_TOKEN {conf.fingerprint(conf.arkesel_whatsapp_token())}')
    # Telegram
    telegram = conf.telegram_enabled()
    add(OK, f'ADMIN_TELEGRAM_NOTIFICATIONS_ENABLED={telegram}')
    if telegram:
        parsed = conf.parse_telegram_chat_ids(getattr(settings, 'ADMIN_TELEGRAM_CHAT_IDS', ''))
        for bad in parsed.invalid:
            add(ERROR, 'ADMIN_TELEGRAM_CHAT_IDS has a value that is not a numeric chat id '
                       '(use manage.py telegram_find_chats to get it).')
        if not parsed.valid:
            add(ERROR, 'Telegram is enabled but ADMIN_TELEGRAM_CHAT_IDS has no valid chat id.')
        else:
            add(OK, f'Telegram chats: {len(parsed.valid)}')
        if not conf.telegram_bot_token():
            add(ERROR, 'Telegram is enabled but TELEGRAM_BOT_TOKEN is not set.')
        else:
            add(OK, f'TELEGRAM_BOT_TOKEN {conf.fingerprint(conf.telegram_bot_token())}')
    if not sms and not whatsapp and not telegram:
        add(WARN, 'Notifications are on but both channels are off: events are recorded, nothing is sent.')
    return report


def admin_notification_config_check(app_configs=None, **kwargs):
    messages = []
    try:
        report = validate_configuration()
    except Exception as exc:  # pragma: no cover - a check must not crash startup
        return [checks.Warning(f'Admin notification config could not be validated: {type(exc).__name__}',
                               id='admin_notifications.W000')]
    for index, (level, text) in enumerate(report):
        if level in (WARN, ERROR):
            messages.append(checks.Warning(text, id=f'admin_notifications.W{index + 1:03d}'))
    return messages
