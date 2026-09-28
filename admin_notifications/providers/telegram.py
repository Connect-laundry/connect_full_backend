"""TelegramProvider: the official Telegram Bot API (https://core.telegram.org/bots/api).

    POST https://api.telegram.org/bot<token>/sendMessage
    body  {"chat_id": <id>, "text": "...", "disable_web_page_preview": true}
    200   {"ok": true, "result": {"message_id": 123, ...}}
    error {"ok": false, "error_code": 400|401|403|404|429, "description": "...",
           "parameters": {"retry_after": 5, "migrate_to_chat_id": -100...}}

The bot token is part of the URL, so the URL is never logged or put in an
error message; ``post_json`` reports only a transport category.

Plain text (no parse_mode) on purpose: customer-typed names, addresses and
notes can contain Markdown/HTML characters, and a formatting error would turn
into a rejected message. Telegram still makes the map and admin URLs clickable.
"""
from __future__ import annotations

from .. import conf
from . import base
from .arkesel_sms import transport_result
from .http import post_json

PROVIDER = 'telegram'
MAX_TEXT = 4096


def _description(body) -> str:
    return base.safe_provider_message(body.get('description')) if isinstance(body, dict) else ''


class TelegramProvider:
    name = PROVIDER

    def missing_config(self) -> list[str]:
        return [] if conf.telegram_bot_token() else ['TELEGRAM_BOT_TOKEN']

    def method_url(self, method: str) -> str:
        return f'{conf.telegram_api_base_url()}/bot{conf.telegram_bot_token()}/{method}'

    def send(self, chat_id: str, text: str) -> base.SendResult:
        missing = self.missing_config()
        if missing:
            return base.SendResult(base.FAILED, error_class=base.CONFIG_MISSING,
                                   safe_message='Missing: ' + ', '.join(missing))
        if len(text) > MAX_TEXT:
            return base.SendResult(base.FAILED, error_class=base.VALIDATION,
                                   safe_message=f'Message part longer than {MAX_TEXT} characters.')
        outcome = post_json(
            self.method_url('sendMessage'), headers={},
            payload={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True},
            timeout=conf.http_timeouts(),
        )
        return self.interpret(outcome, chat_id)

    @staticmethod
    def interpret(outcome, chat_id: str) -> base.SendResult:
        status = outcome.status_code or 0
        body = outcome.body if isinstance(outcome.body, dict) else {}
        if status == 429:
            retry_after = (body.get('parameters') or {}).get('retry_after')
            return base.SendResult(base.RETRYABLE, error_class=base.RATE_LIMITED,
                                   safe_message=f'Telegram rate limit; retry after {retry_after or "?"}s.',
                                   http_status=status)
        generic = transport_result(outcome)
        if generic is not None:
            return generic
        description = _description(body)
        if 200 <= status < 300:
            if not outcome.json_ok:
                return base.SendResult(base.UNKNOWN, error_class=base.MALFORMED_RESPONSE,
                                       safe_message='HTTP 2xx with an unreadable body.', http_status=status)
            result = body.get('result') if isinstance(body.get('result'), dict) else {}
            if body.get('ok') is True and result.get('message_id') is not None:
                # message_id is only unique within a chat, so it is scoped.
                return base.SendResult(base.ACCEPTED, provider_message_id=f"{chat_id}:{result['message_id']}"[:128],
                                       http_status=status)
            return base.SendResult(base.UNKNOWN, error_class=base.MALFORMED_RESPONSE,
                                   safe_message='Telegram answered 2xx without a message id.', http_status=status)
        if status in (401, 404):
            # Telegram answers 404 for a malformed token and 401 for a revoked one.
            return base.SendResult(base.FAILED, error_class=base.AUTH_FAILED,
                                   safe_message=description or 'Bot token rejected (check TELEGRAM_BOT_TOKEN).',
                                   http_status=status)
        if status == 403:
            return base.SendResult(base.FAILED, error_class=base.INVALID_RECIPIENT,
                                   safe_message=description or 'Bot is not allowed to post in this chat.',
                                   http_status=status)
        if status == 400:
            migrate_to = (body.get('parameters') or {}).get('migrate_to_chat_id')
            if migrate_to:
                return base.SendResult(
                    base.FAILED, error_class=base.INVALID_RECIPIENT, http_status=status,
                    safe_message=f'Group became a supergroup; set ADMIN_TELEGRAM_CHAT_IDS to {migrate_to}.')
            lowered = description.lower()
            error = base.INVALID_RECIPIENT if 'chat not found' in lowered or 'chat_id' in lowered else base.VALIDATION
            return base.SendResult(base.FAILED, error_class=error, safe_message=description or 'Bad request.',
                                   http_status=status)
        return base.SendResult(base.FAILED, error_class=base.UNEXPECTED_4XX,
                               safe_message=description or f'Unexpected HTTP {status}.', http_status=status)
