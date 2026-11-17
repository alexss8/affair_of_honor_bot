"""Правки карточки боя. Все правки идут под session.card_lock, поэтому устаревшая правка,
задержавшаяся в сети, не перезапишет более свежую карточку или итог боя."""

from aiogram import Bot
from aiogram.types import Message

from bot.domain.models import DuelOutcome, DuelSession
from bot.services.duel_service import DuelService
from bot.telegram.safe_api import safe_edit_markup, safe_edit_text, safe_edit_text_by_id
from bot.telegram.ui import keyboards, texts


async def refresh_card(message: Message, session: DuelSession, *, markup_only: bool = False) -> bool:
    """Перерисовывает карточку по текущему состоянию сессии.

    Returns:
        False, если сообщение удалено или недоступно (сессию нужно сбросить).
    """
    async with session.card_lock:
        if not session.is_active:
            return True
        markup = keyboards.battle_keyboard(session)
        if markup_only:
            return await safe_edit_markup(message, reply_markup=markup)
        return await safe_edit_text(message, texts.render_battle(session), reply_markup=markup)


async def announce_outcome(message: Message, outcome: DuelOutcome, service: DuelService) -> None:
    """Записывает итог боя и показывает итоговую карточку."""
    finished = service.finish(outcome)
    async with outcome.session.card_lock:
        await safe_edit_text(message, texts.render_outcome(finished, service.top_limit))


async def show_cancelled(bot: Bot, session: DuelSession, canceler_name: str) -> None:
    text = (
        f"🚫 <b>ДУЭЛЬ ОТМЕНЕНА</b>\n\n<i>Бой между {session.p1.name} и {session.p2.name} "
        f"аннулирован игроком {canceler_name} (без изменения рейтинга).</i>"
    )
    async with session.card_lock:
        await safe_edit_text_by_id(bot, session.chat_id, session.message_id, text)
