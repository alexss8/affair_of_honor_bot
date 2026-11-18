"""Правки карточки боя. Все правки идут под session.card_lock, поэтому устаревшая правка,
задержавшаяся в сети, не перезапишет более свежую карточку или итог боя."""

import datetime

from aiogram import Bot
from aiogram.types import Chat, Message

from bot.domain.models import DuelOutcome, DuelSession
from bot.services.duel_service import DuelService
from bot.telegram.rate_limiter import find_rate_limiter
from bot.telegram.safe_api import safe_edit_markup, safe_edit_text, safe_edit_text_by_id
from bot.telegram.ui import keyboards, texts


def card_message(bot: Bot, chat_id: int, message_id: int) -> Message:
    """Message для правки карточки, когда известны только chat_id и message_id (после рестарта)."""
    chat = Chat(id=chat_id, type="supergroup")
    return Message(message_id=message_id, date=datetime.datetime.now(), chat=chat).as_(bot)


async def refresh_card(message: Message, session: DuelSession, *, markup_only: bool = False) -> bool:
    """Перерисовывает карточку по текущему состоянию сессии — «последнее состояние побеждает».

    Если правка карточки уже идёт, вызов сразу возвращает True: правящий после своей правки заметит,
    что состояние изменилось, и отправит одну правку со свежим состоянием. Промежуточные состояния пропускаются;
    итог боя (announce_outcome, show_final_notice) так не сливается никогда.

    Returns:
        False, если сообщение удалено или недоступно (сессию нужно сбросить).
    """
    session.card_revision += 1
    if session.card_lock.locked():
        return True
    async with session.card_lock:
        limiter = find_rate_limiter(message.bot)
        while session.is_active:
            if limiter is not None:
                await limiter.wait_ready(message.chat.id)  # рисуем после ожидания — уйдёт свежее состояние
                if not session.is_active:
                    break
            revision = session.card_revision
            markup = keyboards.battle_keyboard(session)
            if markup_only:
                ok = await safe_edit_markup(message, reply_markup=markup)
            else:
                ok = await safe_edit_text(message, texts.render_battle(session), reply_markup=markup)
            if not ok:
                return False
            if session.card_revision == revision:
                break
            markup_only = False  # за время правки состояние изменилось: повторная правка всегда полная
        return True


async def announce_outcome(message: Message, outcome: DuelOutcome, service: DuelService) -> None:
    """Записывает итог боя и показывает итоговую карточку."""
    finished = service.finish(outcome)
    async with outcome.session.card_lock:
        await safe_edit_text(message, texts.render_outcome(finished, service.top_limit))


async def show_final_notice(bot: Bot, session: DuelSession, text: str) -> None:
    """Заменяет карточку снятого боя текстом без кнопок (отмена, пауза, прерывание)."""
    async with session.card_lock:
        await safe_edit_text_by_id(bot, session.chat_id, session.message_id, text)
