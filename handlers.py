import asyncio
import html
import logging
import os
import time
from typing import Optional
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramServerError
from aiogram.filters import ChatMemberUpdatedFilter, Command, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import CallbackQuery, ChatMemberUpdated, ErrorEvent, Message
from config import Config
from engine import DuelEngine
from models import DuelSession
from repository import BaseRepository
from tg_utils import safe_answer_callback, safe_edit_markup, safe_edit_text, safe_reply
import ui

router = Router()
START_TIME = time.time()

async def _duel_timeout_watcher(
    session: DuelSession,
    message: Message,
    engine: DuelEngine,
    repo: BaseRepository,
    config: Config,
) -> None:
    try:
        await asyncio.sleep(config.duel_timeout_sec)
        if engine.get_session(session.chat_id, session.message_id) is not session:
            return

        outcome = engine.resolve_timeout(session)
        stats = repo.get_top(session.chat_id, config.top_limit)
        if outcome is not None:
            winner, loser = outcome
            duel_id = repo.record_duel(session.chat_id, winner, loser)
            engine.logger.info(
                f"Дуэль #{duel_id} завершена по таймауту (чат {session.chat_id}). "
                f"Победитель: {winner.name} ({winner.hp} HP), Проигравший: {loser.name} ({loser.hp} HP)"
            )
            reason = (
                f"⏰ Время дуэли вышло! {winner.name} ({winner.hp} ❤️) "
                f"оказался сильнее {loser.name} ({loser.hp} ❤️)."
            )
            text = ui.render_victory(duel_id, winner, reason, stats, config.top_limit, icon="⏰")
        else:
            reason = f"⏰ Время дуэли вышло! У обоих бойцов осталось по {session.p1.hp} ❤️."
            text = ui.render_draw(reason, stats, config.top_limit, icon="⏰")

        await safe_edit_text(message, text)
    except asyncio.CancelledError:
        pass

@router.message.outer_middleware()
@router.callback_query.outer_middleware()
async def cache_user_middleware(handler, event, data):
    repo: BaseRepository = data.get("repo")
    user = getattr(event, "from_user", None)
    chat = event.chat if isinstance(event, Message) else getattr(event.message, "chat", None)
    if repo and user and not user.is_bot and chat and chat.type in ("group", "supergroup"):
        repo.save_user(chat.id, user.id, user.username, user.first_name)
    return await handler(event, data)

@router.error()
async def global_error_handler(event: ErrorEvent, logger: logging.Logger, bot: Bot, config: Config):
    exc = event.exception
    if isinstance(exc, (TelegramNetworkError, TelegramServerError, TelegramForbiddenError)):
        logger.warning(f"Сетевая ошибка или ограничение доступа Telegram: {exc}")
        return
    logger.error(f"Непредвиденная ошибка при обработке события: {exc}", exc_info=True)
    if config.admin_id:
        try:
            await bot.send_message(config.admin_id, f"🚨 <b>Ошибка в боте:</b>\n<code>{html.escape(str(exc)[:500])}</code>", parse_mode="HTML")
        except Exception:
            pass

@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> IS_MEMBER))
async def on_bot_added(event: ChatMemberUpdated, config: Config):
    try:
        await event.answer("👋 Всем привет! Я готов проводить дуэли в этом чате.\n\n" + ui.render_help(config.default_hp, config.top_limit), parse_mode="HTML")
    except TelegramForbiddenError:
        pass

@router.message(Command("help", "start"))
async def cmd_help(message: Message, config: Config):
    await message.answer(ui.render_help(config.default_hp, config.top_limit), parse_mode="HTML")

@router.message(Command("top"))
async def cmd_top(message: Message, repo: BaseRepository, config: Config):
    stats = repo.get_top(message.chat.id, config.top_limit)
    await message.answer(ui.render_top(stats, config.top_limit), parse_mode="HTML")

@router.message(Command("status"), F.chat.type == "private")
async def cmd_status(message: Message, engine: DuelEngine, repo: BaseRepository, config: Config):
    if not config.admin_id or message.from_user.id != config.admin_id:
        return
    uptime_sec = int(time.time() - START_TIME)
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    db_stats = repo.get_health_stats()
    errors_count = 0
    last_error = "Ошибок нет ✅"
    if os.path.exists(config.log_file):
        with open(config.log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "[ERROR]" in line:
                    errors_count += 1
                    last_error = html.escape(line.strip()[-180:])
    text = (
        f"🟢 <b>СТАТУС БОТА: ОК</b>\n\n"
        f"⏱ <b>Аптайм:</b> {days}д {hours}ч {minutes}м\n"
        f"⚔️ <b>Активных дуэлей сейчас:</b> {len(engine.sessions)}\n"
        f"📥 <b>Очередь записи в БД:</b> {db_stats.get('queue_size', 0)}\n\n"
        f"🗄 <b>База данных ({db_stats['db_size_kb']} КБ):</b>\n"
        f"• Всего дуэлей сыграно: <b>{db_stats['total_duels']}</b>\n"
        f"• Дуэлей за сегодня (UTC): <b>{db_stats['today_duels']}</b>\n"
        f"• Игроков в кэше ников: <b>{db_stats['cached_users']}</b>\n\n"
        f"📜 <b>Логи ({config.log_file}):</b>\n"
        f"• Записей ERROR: <b>{errors_count}</b>\n"
        f"• Последняя ошибка:\n<code>{last_error}</code>"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, bot: Bot, engine: DuelEngine):
    session = engine.cancel_user_duel(message.chat.id, message.from_user.id)
    if not session:
        return await safe_reply(message, "У тебя сейчас нет активных дуэлей.")
    canceler_name = ui.escape_nick(message.from_user)
    cancel_text = f"🚫 <b>ДУЭЛЬ ОТМЕНЕНА</b>\n\n<i>Бой между {session.p1.name} и {session.p2.name} аннулирован игроком {canceler_name} (без изменения рейтинга).</i>"
    try:
        await bot.edit_message_text(chat_id=session.chat_id, message_id=session.message_id, text=cancel_text, parse_mode="HTML")
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramServerError):
        pass
    await safe_reply(message, "🚫 Твоя дуэль с соперником успешно отменена. Вы оба снова свободны!")

def _resolve_target(message: Message, repo: BaseRepository) -> Optional[tuple[int, str, bool]]:
    if message.reply_to_message and message.reply_to_message.from_user:
        if not message.reply_to_message.forum_topic_created:
            target = message.reply_to_message.from_user
            return target.id, ui.escape_nick(target), target.is_bot
    if message.entities:
        for ent in message.entities:
            if ent.type == "text_mention" and ent.user:
                return ent.user.id, ui.escape_nick(ent.user), ent.user.is_bot
            if ent.type == "mention" and message.text:
                mention_text = message.text[ent.offset : ent.offset + ent.length]
                found = repo.get_user_by_username(message.chat.id, mention_text)
                if found:
                    user_id, display_nick = found
                    return user_id, html.escape(display_nick), False
    return None

@router.message(Command("duel"))
async def cmd_duel(message: Message, engine: DuelEngine, repo: BaseRepository):
    target_data = _resolve_target(message, repo)
    if not target_data:
        return await safe_reply(
            message,
            "Укажи соперника! Напиши <code>/duel @ник</code> (выбрав из списка) или ответь (Reply) командой /duel на его сообщение.\n"
            "<i>(Если вызов по нику не сработал — соперник должен написать хотя бы одно сообщение в группе, чтобы я его запомнил).</i>"
        )
    p1 = message.from_user
    p2_id, p2_name, p2_is_bot = target_data
    cid = message.chat.id
    if p1.id == p2_id or p2_is_bot:
        return await safe_reply(message, "Нельзя вызвать самого себя или бота.")
    if engine.is_user_busy(cid, p1.id) or engine.is_user_busy(cid, p2_id):
        return await safe_reply(message, "Один из игроков уже участвует в другой дуэли!")
    await message.answer(ui.render_invite(ui.escape_nick(p1), p2_name), reply_markup=ui.invite_keyboard(p1.id, p2_id), parse_mode="HTML")

@router.callback_query(F.data.startswith(("accept_", "decline_", "revoke_")))
async def handle_invite(cb: CallbackQuery, bot: Bot, engine: DuelEngine, repo: BaseRepository, config: Config):
    act, p1_id, p2_id = cb.data.split("_")
    p1_id, p2_id, cid = int(p1_id), int(p2_id), cb.message.chat.id
    if act == "revoke":
        if cb.from_user.id != p1_id:
            return await safe_answer_callback(cb, "Отозвать вызов может только тот, кто его бросил!", show_alert=True)
        await safe_answer_callback(cb, "Вызов отозван.")
        return await safe_edit_text(cb.message, "🚫 Инициатор отозвал свой вызов на дуэль.")
    if cb.from_user.id != p2_id:
        return await safe_answer_callback(cb, "Этот вызов адресован не тебе!", show_alert=True)
    if act == "decline":
        await safe_answer_callback(cb)
        return await safe_edit_text(cb.message, "🏳️ Вызов на дуэль был отклонен.")
    if engine.is_user_busy(cid, p1_id) or engine.is_user_busy(cid, p2_id):
        await safe_answer_callback(cb)
        return await safe_edit_text(cb.message, "❌ Один из игроков уже вступил в другой бой.")
    await safe_answer_callback(cb)
    p1_chat = await bot.get_chat(p1_id)
    session = engine.start_duel(cid, cb.message.message_id, p1_id, ui.escape_nick(p1_chat), p2_id, ui.escape_nick(cb.from_user))
    ok = await safe_edit_text(cb.message, ui.render_battle(session), reply_markup=ui.battle_keyboard(session))
    if not ok:
        engine.drop_session(cid, cb.message.message_id)
        return

    session.timeout_task = asyncio.create_task(
        _duel_timeout_watcher(session, cb.message, engine, repo, config)
    )

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
        ok = await safe_edit_markup(cb.message, reply_markup=ui.battle_keyboard(session))
        if not ok:
            engine.drop_session(session.chat_id, session.message_id)
        return
    winner = session.get_opponent(player.id)
    duel_id = engine.finish_duel(session, winner, player)
    stats = repo.get_top(cb.message.chat.id, config.top_limit)
    await safe_answer_callback(cb, "Ты сдался!")
    await safe_edit_text(cb.message, ui.render_victory(duel_id, winner, f"{player.name} выбросил белый флаг и сдался!", stats, config.top_limit, icon="🏳️"))

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
    is_attacker = (player.id == session.attacker.id)
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
        await safe_edit_text(cb.message, ui.render_battle(session, is_rolling=True), reply_markup=ui.battle_keyboard(session, is_rolling=True))
        await asyncio.sleep(0.8)
        if engine.get_session(session.chat_id, session.message_id) is not session:
            return
        log, is_over = engine.resolve_round(session)
        if is_over:
            winner = session.p1 if session.p1.hp > 0 else session.p2
            loser = session.get_opponent(winner.id)
            duel_id = engine.finish_duel(session, winner, loser)
            stats = repo.get_top(cb.message.chat.id, config.top_limit)
            return await safe_edit_text(cb.message, ui.render_victory(duel_id, winner, log, stats, config.top_limit))
        ok = await safe_edit_text(cb.message, ui.render_battle(session, log), reply_markup=ui.battle_keyboard(session))
    else:
        ok = await safe_edit_text(cb.message, ui.render_battle(session), reply_markup=ui.battle_keyboard(session))
    if not ok:
        engine.drop_session(session.chat_id, session.message_id)
