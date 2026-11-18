"""Таймер вызова: по истечении срока без ответа закрывает вызов и помечает карточку «вызов истёк»."""

import asyncio
import time

from aiogram.types import Message

from bot.domain.engine import DuelEngine
from bot.domain.models import DuelInvite
from bot.telegram.safe_api import safe_edit_text
from bot.telegram.ui import texts

# Ссылки на задачи: иначе сборщик мусора может удалить незавершённую задачу asyncio
_tasks: set[asyncio.Task] = set()


def start_invite_timer(invite: DuelInvite, message: Message, engine: DuelEngine) -> None:
    task = asyncio.create_task(_expire_later(invite, message, engine))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _expire_later(invite: DuelInvite, message: Message, engine: DuelEngine) -> None:
    await asyncio.sleep(max(0.0, invite.expires_at - time.time()))
    # Принятый, отклонённый, отозванный или снятый при остановке вызов уже закрыт — тогда ничего не делаем
    if engine.close_invite(invite):
        await safe_edit_text(message, texts.INVITE_EXPIRED)
