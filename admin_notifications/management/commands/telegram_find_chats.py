import requests
from django.core.management.base import BaseCommand, CommandError

from admin_notifications import conf
from admin_notifications.providers.telegram import TelegramProvider


class Command(BaseCommand):
    help = (
        'List the Telegram chats the bot has seen recently (from getUpdates), to find the numeric '
        'chat id for ADMIN_TELEGRAM_CHAT_IDS. Add the bot to your group and send any message in it '
        '(e.g. /start) first. Read-only: sends nothing.'
    )

    def handle(self, *args, **options):
        if not conf.telegram_bot_token():
            raise CommandError('TELEGRAM_BOT_TOKEN is not set.')
        provider = TelegramProvider()
        try:
            # The URL contains the token: never print it or the exception text.
            response = requests.get(provider.method_url('getUpdates'), params={'timeout': 0},
                                    timeout=conf.http_timeouts(), verify=True, allow_redirects=False)
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CommandError(f'Telegram request failed: {type(exc).__name__}')
        if response.status_code != 200 or not body.get('ok'):
            raise CommandError(f"Telegram getUpdates HTTP {response.status_code}: "
                               f"{str(body.get('description', ''))[:120]}")

        chats = {}
        for update in body.get('result') or []:
            for key in ('message', 'edited_message', 'channel_post', 'my_chat_member', 'chat_member'):
                chat = (update.get(key) or {}).get('chat')
                if chat and chat.get('id') is not None:
                    chats[chat['id']] = chat
        if not chats:
            self.stdout.write('No chats seen yet. Add the bot to the group, send "/start" in the group, '
                              'then run this again.')
            return
        for chat_id, chat in chats.items():
            name = chat.get('title') or ' '.join(filter(None, [chat.get('first_name'), chat.get('last_name')]))
            self.stdout.write(f"{chat_id}\t{chat.get('type')}\t{name}")
        self.stdout.write('Use the id of your ops group (type group/supergroup) for ADMIN_TELEGRAM_CHAT_IDS.')
