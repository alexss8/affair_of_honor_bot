import html
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import DuelInvite
from bot.services.duel_service import DuelService
from bot.storage.base import BaseRepository
from bot.telegram.callbacks import InviteAction, InviteCb
from bot.telegram.duel_cards import refresh_card
from bot.telegram.duel_timer import start_duel_timer
from bot.telegram.invite_timer import start_invite_timer
from bot.telegram.safe_api import safe_answer, safe_answer_callback, safe_edit_text, safe_reply
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
    p1_name = texts.escape_nick(p1)
    p2_id, p2_name, p2_is_bot = target_data
    cid = message.chat.id
    if p1.id == p2_id or p2_is_bot:
        return await safe_reply(message, "Нельзя вызвать самого себя или бота.")
    if engine.is_user_busy(cid, p1.id) or engine.is_user_busy(cid, p2_id):
        return await safe_reply(message, "Один из игроков уже участвует в другой дуэли!")
    if not engine.reserve_invite(cid, p1.id):
        return await safe_reply(message, "У тебя уже есть открытый вызов — дождись ответа или отзови его.")
    sent = None
    try:
        sent = await safe_answer(
            message, texts.render_invite(p1_name, p2_name), reply_markup=keyboards.invite_keyboard()
        )
    finally:
        if sent is None:  # карточку отправить не удалось — бронь не должна блокировать следующий /duel
            engine.release_invite_reservation(cid, p1.id)
    if sent is not None:
        invite = engine.create_invite(cid, sent.message_id, p1.id, p1_name, p2_id, p2_name)
        start_invite_timer(invite, sent, engine)


@router.callback_query(InviteCb.filter(F.action == InviteAction.REVOKE))
async def handle_revoke(cb: CallbackQuery, engine: DuelEngine):
    invite = await _get_live_invite(cb, engine)
    if invite is None:
        return
    if cb.from_user.id != invite.challenger_id:
        return await safe_answer_callback(cb, "Отозвать вызов может только тот, кто его бросил!", show_alert=True)
    engine.close_invite(invite)
    await safe_answer_callback(cb, "Вызов отозван.")
    await safe_edit_text(cb.message, "🚫 Инициатор отозвал свой вызов на дуэль.")


@router.callback_query(InviteCb.filter(F.action == InviteAction.DECLINE))
async def handle_decline(cb: CallbackQuery, engine: DuelEngine):
    invite = await _get_live_invite(cb, engine)
    if invite is None:
        return
    if cb.from_user.id != invite.target_id:
        return await safe_answer_callback(cb, "Этот вызов адресован не тебе!", show_alert=True)
    engine.close_invite(invite)
    await safe_answer_callback(cb)
    await safe_edit_text(cb.message, "🏳️ Вызов на дуэль был отклонен.")


@router.callback_query(InviteCb.filter(F.action == InviteAction.ACCEPT))
async def handle_accept(cb: CallbackQuery, engine: DuelEngine, service: DuelService, config: Config):
    invite = await _get_live_invite(cb, engine)
    if invite is None:
        return
    if cb.from_user.id != invite.target_id:
        return await safe_answer_callback(cb, "Этот вызов адресован не тебе!", show_alert=True)
    # accept_invite синхронный: проверка занятости и закрытие приглашения атомарны,
    # второй клик «Принять» найдёт приглашение уже закрытым.
    session = engine.accept_invite(invite)
    if session is None:
        if engine.close_invite(invite):
            await safe_answer_callback(cb)
            return await safe_edit_text(cb.message, "❌ Один из игроков уже вступил в другой бой.")
        return await safe_answer_callback(cb, "Дуэль уже началась!")
    await safe_answer_callback(cb)
    if not await refresh_card(cb.message, session):
        engine.drop_session(session.chat_id, session.message_id)
        return
    start_duel_timer(session, cb.message, engine, service, config.duel_timeout_sec)


async def _get_live_invite(cb: CallbackQuery, engine: DuelEngine) -> DuelInvite | None:
    """Находит действующее приглашение по сообщению; отвечает на клик, если его нет или оно истекло."""
    invite = engine.get_invite(cb.message.chat.id, cb.message.message_id)
    if invite is None:
        if engine.get_session(cb.message.chat.id, cb.message.message_id):
            await safe_answer_callback(cb, "Дуэль уже началась!")
        else:
            await safe_answer_callback(cb, "Этот вызов больше не действителен. Брось новый: /duel", show_alert=True)
        return None
    if invite.is_expired(time.time()):
        engine.close_invite(invite)
        await safe_answer_callback(cb, "Вызов истёк.", show_alert=True)
        await safe_edit_text(cb.message, texts.INVITE_EXPIRED)
        return None
    return invite
