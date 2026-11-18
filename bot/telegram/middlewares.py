from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.types import Chat, Message, TelegramObject

from bot.logger import log_chat
from bot.storage.base import BaseRepository

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def event_chat(event: TelegramObject | None) -> Chat | None:
    """Чат события: у Message и ChatMemberUpdated — event.chat, у CallbackQuery — чат его сообщения."""
    if isinstance(event, Message):
        return event.chat
    chat = getattr(event, "chat", None)
    if chat is not None:
        return chat
    return getattr(getattr(event, "message", None), "chat", None)


async def log_context_middleware(handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
    """Записи лога при обработке события получают chat_id (см. bot/logger.py)."""
    chat = event_chat(event)
    with log_chat(chat.id if chat else None):
        return await handler(event, data)


async def cache_user_middleware(handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
    """Запоминает автора сообщения/нажатия в кэше участников группы (для вызова по @username)."""
    repo: BaseRepository | None = data.get("repo")
    user = getattr(event, "from_user", None)
    chat = event_chat(event)
    if repo and user and not user.is_bot and chat and chat.type in ("group", "supergroup"):
        repo.save_user(chat.id, user.id, user.username, user.first_name)
    return await handler(event, data)
