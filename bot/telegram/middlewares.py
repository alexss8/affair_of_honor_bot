from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.types import Message, TelegramObject

from bot.storage.base import BaseRepository


async def cache_user_middleware(
    handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
    event: TelegramObject,
    data: dict[str, Any],
) -> Any:
    """Запоминает автора сообщения/нажатия в кэше участников группы (для вызова по @username)."""
    repo: BaseRepository | None = data.get("repo")
    user = getattr(event, "from_user", None)
    chat = event.chat if isinstance(event, Message) else getattr(getattr(event, "message", None), "chat", None)
    if repo and user and not user.is_bot and chat and chat.type in ("group", "supergroup"):
        repo.save_user(chat.id, user.id, user.username, user.first_name)
    return await handler(event, data)
