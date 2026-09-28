from .base import SendResult  # noqa: F401


def get_sms_provider():
    from .arkesel_sms import ArkeselSmsProvider
    return ArkeselSmsProvider()


def get_whatsapp_provider():
    from .arkesel_whatsapp import ArkeselWhatsAppProvider
    return ArkeselWhatsAppProvider()


def get_telegram_provider():
    from .telegram import TelegramProvider
    return TelegramProvider()
