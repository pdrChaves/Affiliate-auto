from ..config import Settings
from .base import NullNotifier


def build_notifier(settings: Settings):
    if settings.telegram_bot_token and settings.telegram_chat_id:
        from .telegram import TelegramNotifier
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return NullNotifier()
