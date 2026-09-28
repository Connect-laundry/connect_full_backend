import requests
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from admin_notifications import conf
from admin_notifications.checks import ERROR, OK, WARN, validate_configuration


class Command(BaseCommand):
    help = 'Validate admin notification configuration without printing secrets. Exit 1 on errors.'

    def add_arguments(self, parser):
        parser.add_argument('--probe', action='store_true',
                            help='Also call Arkesel read-only endpoints: SMS balance and approved WhatsApp '
                                 'templates. Sends no message.')

    def handle(self, *args, **options):
        report = validate_configuration()
        report.append(self._migration_state())
        if options['probe'] and conf.notifications_enabled():
            report.extend(self._probe())
        styles = {OK: self.style.SUCCESS, WARN: self.style.WARNING, ERROR: self.style.ERROR}
        for level, message in report:
            self.stdout.write(styles[level](f'[{level}] {message}'))
        if any(level == ERROR for level, _ in report):
            raise SystemExit(1)

    def _migration_state(self):
        try:
            executor = MigrationExecutor(connection)
            pending = [m for m, _ in executor.migration_plan(executor.loader.graph.leaf_nodes())
                       if m.app_label == 'admin_notifications']
        except Exception as exc:
            return WARN, f'Could not inspect migrations: {type(exc).__name__}'
        if pending:
            return ERROR, f'admin_notifications has {len(pending)} unapplied migration(s).'
        return OK, 'admin_notifications migrations applied.'

    def _probe(self):
        out = []
        timeout = conf.http_timeouts()
        if conf.sms_enabled() and conf.arkesel_api_key():
            try:
                response = requests.get(
                    conf.arkesel_sms_base_url() + '/api/v2/clients/balance-details',
                    headers={'api-key': conf.arkesel_api_key(), 'Accept': 'application/json'},
                    timeout=timeout, verify=True, allow_redirects=False,
                )
                data = response.json() if response.status_code == 200 else {}
                body = data.get('data') if isinstance(data.get('data'), dict) else data
                balance = body.get('sms_balance', body.get('balance')) if isinstance(body, dict) else None
                if response.status_code == 200:
                    out.append((OK, f'Arkesel key accepted; SMS balance: {balance}'))
                else:
                    out.append((ERROR, f'Arkesel balance check HTTP {response.status_code} (key/account problem).'))
            except (requests.RequestException, ValueError) as exc:
                out.append((WARN, f'Arkesel balance check failed: {type(exc).__name__}'))
        if conf.telegram_enabled() and conf.telegram_bot_token():
            out.extend(_probe_telegram(timeout))
        if conf.whatsapp_enabled() and conf.arkesel_whatsapp_token() and conf.arkesel_whatsapp_org_id():
            try:
                response = requests.get(
                    f'{conf.arkesel_whatsapp_base_url()}/ext-api/v1/whatsapp-business/'
                    f'{conf.arkesel_whatsapp_org_id()}/templates',
                    headers={'X-API-Token': conf.arkesel_whatsapp_token(), 'Accept': 'application/json'},
                    timeout=timeout, verify=True, allow_redirects=False,
                )
                if response.status_code != 200:
                    out.append((ERROR, f'WhatsApp template listing HTTP {response.status_code}.'))
                    return out
                templates = response.json().get('data') or []
                for key in ('new_order', 'order_update'):
                    wanted = conf.whatsapp_template_id(key)
                    match = next((t for t in templates if wanted and wanted in (
                        str(t.get('afr_uuid')), str(t.get('template_id')))), None)
                    if match is None:
                        out.append((ERROR, f'WhatsApp template for {key} not found in this organization.'))
                    elif str(match.get('status', '')).upper() != 'APPROVED':
                        out.append((ERROR, f'WhatsApp template {match.get("name")} status {match.get("status")}: '
                                           'WHATSAPP TEMPLATE APPROVAL PENDING.'))
                    else:
                        out.append((OK, f'WhatsApp template {match.get("name")} APPROVED '
                                        f'({match.get("category")}).'))
            except (requests.RequestException, ValueError, AttributeError) as exc:
                out.append((WARN, f'WhatsApp template check failed: {type(exc).__name__}'))
        return out


def _probe_telegram(timeout):
    """getMe proves the token; getChat proves the bot can see each chat. Sends nothing."""
    from admin_notifications.phone import mask_recipient
    from admin_notifications.providers.telegram import TelegramProvider
    provider = TelegramProvider()
    out = []
    try:
        # The URL holds the token: never include the exception text or URL in output.
        me = requests.get(provider.method_url('getMe'), timeout=timeout, verify=True, allow_redirects=False)
        body = me.json() if me.headers.get('content-type', '').startswith('application/json') else {}
        if me.status_code == 200 and body.get('ok'):
            out.append((OK, f"Telegram bot @{body['result'].get('username')} token accepted."))
        else:
            out.append((ERROR, f'Telegram getMe HTTP {me.status_code}: token rejected.'))
            return out
        for chat_id in conf.telegram_chat_ids():
            chat = requests.get(provider.method_url('getChat'), params={'chat_id': chat_id}, timeout=timeout,
                                verify=True, allow_redirects=False)
            data = chat.json() if chat.headers.get('content-type', '').startswith('application/json') else {}
            if chat.status_code == 200 and data.get('ok'):
                result = data['result']
                out.append((OK, f"Telegram {mask_recipient(chat_id)}: '{result.get('title', '')}' "
                                f"({result.get('type')}) reachable by the bot."))
            else:
                out.append((ERROR, f'Telegram {mask_recipient(chat_id)}: HTTP {chat.status_code} '
                                   f"{str(data.get('description', ''))[:120]}"))
    except (requests.RequestException, ValueError, KeyError) as exc:
        out.append((WARN, f'Telegram probe failed: {type(exc).__name__}'))
    return out
