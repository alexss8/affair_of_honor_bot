import asyncio

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.domain.engine import DuelEngine
from bot.domain.models import ChoiceResult, DuelPhase, DuelSession, Player
from bot.services.duel_service import DuelService
from bot.telegram.callbacks import MoveCb, SurrenderCb
from bot.telegram.duel_cards import announce_outcome, refresh_card, show_final_notice
from bot.telegram.rate_limiter import RateLimiter
from bot.telegram.safe_api import safe_answer_callback, safe_reply
from bot.telegram.ui import texts

router = Router(name="battle")

ROLL_DELAY_SEC = 0.8
# Обычное окно чата (3 правки за 3 с) задерживает карточку до ~2 с — это ещё не притормаживание
SLOW_HINT_SEC = 3.0

# Ответ игроку на нажатие кнопки хода: (текст, показать как alert)
_CHOICE_REPLIES: dict[ChoiceResult, tuple[str, bool]] = {
    ChoiceResult.ACCEPTED: ("Выбор принят! Ждем соперника.", False),
    ChoiceResult.ROUND_READY: ("Выбор принят! Ждем соперника.", False),
    ChoiceResult.ROUND_IN_PROGRESS: ("Раунд уже рассчитывается, подожди секунду!", False),
    ChoiceResult.MUST_ATTACK: ("Сейчас твой ход АТАКОВАТЬ! Выбери один из ударов.", True),
    ChoiceResult.MUST_DEFEND: ("Сейчас твой ход ЗАЩИЩАТЬСЯ! Выбери один из блоков.", True),
    ChoiceResult.ALREADY_CHOSEN: ("Ты уже выбрал это действие! Ждем соперника.", False),
}


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, bot: Bot, engine: DuelEngine):
    session = engine.cancel_user_duel(message.chat.id, message.from_user.id)
    if not session:
        return await safe_reply(message, "У тебя сейчас нет активных дуэлей.")
    await show_final_notice(bot, session, texts.render_cancelled(session, texts.escape_nick(message.from_user)))
    await safe_reply(message, "🚫 Твоя дуэль с соперником успешно отменена. Вы оба снова свободны!")


async def _get_player_session(cb: CallbackQuery, engine: DuelEngine) -> tuple[DuelSession, Player] | None:
    """Сессия и игрок по нажатой кнопке; отвечает на клик, если дуэли нет или нажал посторонний."""
    session = engine.get_session(cb.message.chat.id, cb.message.message_id)
    if not session:
        await safe_answer_callback(cb, "Эта дуэль уже завершена.", show_alert=True)
        return None
    player = session.get_player(cb.from_user.id)
    if not player:
        await safe_answer_callback(cb, "Это не ваша дуэль!", show_alert=True)
        return None
    return session, player


@router.callback_query(SurrenderCb.filter())
async def handle_surrender(cb: CallbackQuery, engine: DuelEngine, service: DuelService):
    found = await _get_player_session(cb, engine)
    if found is None:
        return
    session, player = found
    if not engine.request_surrender(session, player):
        await safe_answer_callback(cb, "Нажми на кнопку еще раз, чтобы подтвердить сдачу!", show_alert=True)
        if not await refresh_card(cb.message, session, markup_only=True):
            engine.drop_session(session.chat_id, session.message_id)
        return
    outcome = engine.surrender(session, player)
    await safe_answer_callback(cb, "Ты сдался!")
    await announce_outcome(cb.message, outcome, service)


@router.callback_query(MoveCb.filter())
async def handle_move(
    cb: CallbackQuery, callback_data: MoveCb, engine: DuelEngine, service: DuelService, limiter: RateLimiter
):
    found = await _get_player_session(cb, engine)
    if found is None:
        return
    session, player = found
    result = engine.choose(session, player, callback_data.kind, callback_data.zone)
    reply, show_alert = _CHOICE_REPLIES[result]
    card_changes = result in (ChoiceResult.ACCEPTED, ChoiceResult.ROUND_READY)
    if card_changes:
        wait_sec = limiter.estimate_wait(cb.message.chat.id)
        if wait_sec >= SLOW_HINT_SEC:
            reply = texts.render_slow_choice(wait_sec)
    await safe_answer_callback(cb, reply, show_alert=show_alert)
    if not card_changes:
        return

    ok = await refresh_card(cb.message, session)
    if ok and result is ChoiceResult.ROUND_READY:
        await asyncio.sleep(ROLL_DELAY_SEC)
        if session.phase is not DuelPhase.ROLLING:
            return  # за время броска бой завершили сдачей, отменой или по таймауту
        outcome = engine.resolve_round(session)
        if outcome is not None:
            return await announce_outcome(cb.message, outcome, service)
        ok = await refresh_card(cb.message, session)
    if not ok:
        engine.drop_session(session.chat_id, session.message_id)
