import html

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.storage.base import BaseRepository
from bot.telegram.duel_timer import start_duel_timer
from bot.telegram.safe_api import safe_answer, safe_answer_callback, safe_edit_text, safe_get_chat, safe_reply
from bot.telegram.ui import keyboards, texts

router = Router(name="invite")


def _resolve_target(message: Message, repo: BaseRepository) -> tuple[int, str, bool] | None:
    """Определяет соперника по Reply, text_mention или @username. Возвращает (id, ник, is_bot)."""
    if message.reply_to_message and message.reply_to_message.from_user:
        if not message.reply_to_message.forum_topic_created:
            target = message.reply_to_message.from_user
            return target.id, texts.escape_nick(target), target.is_bot
    if message.entities:
        for ent in message.entities:
            if ent.type == "text_mention" and ent.user:
                return ent.user.id, texts.escape_nick(ent.user), ent.user.is_bot
            if ent.type == "mention" and message.text:
                mention_text = message.text[ent.offset : ent.offset + ent.length]
                found = repo.get_user_by_username(message.chat.id, mention_text)
                if found:
                    user_id, display_nick = found
                    return user_id, html.escape(display_nick), False
    return None


async def _resolve_user_nick(bot: Bot, repo: BaseRepository, chat_id: int, user_id: int) -> str:
    """Ник игрока: из кэша участников чата, иначе через get_chat, иначе заглушка."""
    cached = repo.get_user_names(chat_id, user_id)
    if cached:
        return texts.format_nick(*cached)
    chat = await safe_get_chat(bot, user_id)
    if chat:
        return texts.escape_nick(chat)
    return texts.format_nick(None, None)


@router.message(Command("duel"))
async def cmd_duel(message: Message, engine: DuelEngine, repo: BaseRepository):
    target_data = _resolve_target(message, repo)
    if not target_data:
        return await safe_reply(
            message,
            "Укажи соперника! Напиши <code>/duel @ник</code> (выбрав из списка) "
            "или ответь (Reply) командой /duel на его сообщение.\n"
            "<i>(Если вызов по нику не сработал — соперник должен написать хотя бы одно сообщение в группе, "
            "чтобы я его запомнил).</i>",
        )
    p1 = message.from_user
    p2_id, p2_name, p2_is_bot = target_data
    cid = message.chat.id
    if p1.id == p2_id or p2_is_bot:
        return await safe_reply(message, "Нельзя вызвать самого себя или бота.")
    if engine.is_user_busy(cid, p1.id) or engine.is_user_busy(cid, p2_id):
        return await safe_reply(message, "Один из игроков уже участвует в другой дуэли!")
    await safe_answer(
        message,
        texts.render_invite(texts.escape_nick(p1), p2_name),
        reply_markup=keyboards.invite_keyboard(p1.id, p2_id),
    )


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
    msg_id = cb.message.message_id
    if engine.get_session(cid, msg_id):
        return await safe_answer_callback(cb, "Дуэль уже началась!")
    p1_name = await _resolve_user_nick(bot, repo, cid, p1_id)
    # Между проверками и созданием сессии не должно быть await: иначе двойной клик создаст две дуэли.
    if engine.get_session(cid, msg_id):
        return await safe_answer_callback(cb, "Дуэль уже началась!")
    if engine.is_user_busy(cid, p1_id) or engine.is_user_busy(cid, p2_id):
        await safe_answer_callback(cb)
        return await safe_edit_text(cb.message, "❌ Один из игроков уже вступил в другой бой.")
    session = engine.start_duel(cid, msg_id, p1_id, p1_name, p2_id, texts.escape_nick(cb.from_user))
    await safe_answer_callback(cb)
    ok = await safe_edit_text(cb.message, texts.render_battle(session), reply_markup=keyboards.battle_keyboard(session))
    if not ok:
        engine.drop_session(cid, msg_id)
        return
    start_duel_timer(session, cb.message, engine, repo, config)
