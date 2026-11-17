import asyncio

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.storage.base import BaseRepository
from bot.telegram.safe_api import (
    safe_answer_callback,
    safe_edit_markup,
    safe_edit_text,
    safe_edit_text_by_id,
    safe_reply,
)
from bot.telegram.ui import keyboards, texts

router = Router(name="battle")

ROLL_DELAY_SEC = 0.8


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, bot: Bot, engine: DuelEngine):
    session = engine.cancel_user_duel(message.chat.id, message.from_user.id)
    if not session:
        return await safe_reply(message, "У тебя сейчас нет активных дуэлей.")
    canceler_name = texts.escape_nick(message.from_user)
    cancel_text = (
        f"🚫 <b>ДУЭЛЬ ОТМЕНЕНА</b>\n\n<i>Бой между {session.p1.name} и {session.p2.name} "
        f"аннулирован игроком {canceler_name} (без изменения рейтинга).</i>"
    )
    await safe_edit_text_by_id(bot, session.chat_id, session.message_id, cancel_text)
    await safe_reply(message, "🚫 Твоя дуэль с соперником успешно отменена. Вы оба снова свободны!")


@router.callback_query(F.data == "surrender")
async def handle_surrender(cb: CallbackQuery, engine: DuelEngine, repo: BaseRepository, config: Config):
    session = engine.get_session(cb.message.chat.id, cb.message.message_id)
    if not session:
        return await safe_answer_callback(cb, "Эта дуэль уже завершена.", show_alert=True)
    player = session.get_player(cb.from_user.id)
    if not player:
        return await safe_answer_callback(cb, "Это не ваша дуэль!", show_alert=True)
    if session.surrender_confirm_id != player.id:
        session.surrender_confirm_id = player.id
        await safe_answer_callback(cb, "Нажми на кнопку еще раз, чтобы подтвердить сдачу!", show_alert=True)
        ok = await safe_edit_markup(cb.message, reply_markup=keyboards.battle_keyboard(session))
        if not ok:
            engine.drop_session(session.chat_id, session.message_id)
        return
    winner = session.get_opponent(player.id)
    duel_id = engine.finish_duel(session, winner, player)
    stats = repo.get_top(cb.message.chat.id, config.top_limit)
    await safe_answer_callback(cb, "Ты сдался!")
    reason = f"{player.name} выбросил белый флаг и сдался!"
    await safe_edit_text(cb.message, texts.render_victory(duel_id, winner, reason, stats, config.top_limit, icon="🏳️"))


@router.callback_query(F.data.startswith(("act_", "def_")))
async def handle_turn(cb: CallbackQuery, engine: DuelEngine, repo: BaseRepository, config: Config):
    session = engine.get_session(cb.message.chat.id, cb.message.message_id)
    if not session:
        return await safe_answer_callback(cb, "Эта дуэль уже завершена.", show_alert=True)
    player = session.get_player(cb.from_user.id)
    if not player:
        return await safe_answer_callback(cb, "Это не ваша дуэль!", show_alert=True)
    if session.p1.choice and session.p2.choice:
        return await safe_answer_callback(cb, "Раунд уже рассчитывается, подожди секунду!")
    act_type, direction = cb.data.split("_")
    is_attacker = player.id == session.attacker.id
    if is_attacker and act_type != "act":
        return await safe_answer_callback(cb, "Сейчас твой ход АТАКОВАТЬ! Выбери один из ударов.", show_alert=True)
    if not is_attacker and act_type != "def":
        return await safe_answer_callback(cb, "Сейчас твой ход ЗАЩИЩАТЬСЯ! Выбери один из блоков.", show_alert=True)
    if player.choice == direction and session.surrender_confirm_id is None:
        return await safe_answer_callback(cb, "Ты уже выбрал это действие! Ждем соперника.")
    player.choice = direction
    session.surrender_confirm_id = None
    await safe_answer_callback(cb, "Выбор принят! Ждем соперника.")
    if session.p1.choice and session.p2.choice:
        await safe_edit_text(
            cb.message,
            texts.render_battle(session, is_rolling=True),
            reply_markup=keyboards.battle_keyboard(session, is_rolling=True),
        )
        await asyncio.sleep(ROLL_DELAY_SEC)
        if engine.get_session(session.chat_id, session.message_id) is not session:
            return
        log, is_over = engine.resolve_round(session)
        if is_over:
            winner = session.p1 if session.p1.hp > 0 else session.p2
            loser = session.get_opponent(winner.id)
            duel_id = engine.finish_duel(session, winner, loser)
            stats = repo.get_top(cb.message.chat.id, config.top_limit)
            return await safe_edit_text(cb.message, texts.render_victory(duel_id, winner, log, stats, config.top_limit))
        ok = await safe_edit_text(
            cb.message, texts.render_battle(session, log), reply_markup=keyboards.battle_keyboard(session)
        )
    else:
        ok = await safe_edit_text(
            cb.message, texts.render_battle(session), reply_markup=keyboards.battle_keyboard(session)
        )
    if not ok:
        engine.drop_session(session.chat_id, session.message_id)
