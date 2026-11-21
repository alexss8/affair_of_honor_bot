import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.services import system_metrics
from bot.storage.base import BaseRepository
from bot.telegram.callbacks import TopResetAction, TopResetCb
from bot.telegram.safe_api import (
    safe_answer,
    safe_answer_callback,
    safe_edit_text,
    safe_get_chat_member,
    safe_reply,
)
from bot.telegram.ui import keyboards, texts

router = Router(name="admin")

_GROUP_ADMIN_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def _can_reset_top(bot: Bot, chat_id: int, user_id: int, config: Config) -> bool:
    """Обнулять топ могут владелец бота (ADMIN_ID) и админы этой группы; права проверяются у Telegram."""
    if config.admin_id and user_id == config.admin_id:
        return True
    member = await safe_get_chat_member(bot, chat_id, user_id)
    return member is not None and member.status in _GROUP_ADMIN_STATUSES


@router.message(Command("resettop"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_reset_top(message: Message, bot: Bot, config: Config):
    admin_id = message.from_user.id
    if not await _can_reset_top(bot, message.chat.id, admin_id, config):
        await safe_reply(message, texts.TOP_RESET_DENIED)
        return
    await safe_answer(message, texts.TOP_RESET_CONFIRM, reply_markup=keyboards.top_reset_keyboard(admin_id))


@router.callback_query(TopResetCb.filter())
async def on_top_reset(
    cb: CallbackQuery,
    callback_data: TopResetCb,
    bot: Bot,
    repo: BaseRepository,
    config: Config,
    logger: logging.Logger,
):
    if cb.from_user.id != callback_data.admin_id:
        await safe_answer_callback(cb, texts.TOP_RESET_NOT_YOURS, show_alert=True)
        return
    if callback_data.action == TopResetAction.CANCEL:
        await safe_answer_callback(cb)
        await safe_edit_text(cb.message, texts.TOP_RESET_CANCELLED)
        return
    chat_id = cb.message.chat.id
    # Rights are checked again: the admin may have been demoted since the command.
    if not await _can_reset_top(bot, chat_id, cb.from_user.id, config):
        await safe_answer_callback(cb, texts.TOP_RESET_DENIED, show_alert=True)
        return
    removed = repo.reset_top(chat_id)
    logger.info("Топ чата обнулён админом %s, убрано игроков: %s", cb.from_user.id, removed)
    await safe_answer_callback(cb)
    await safe_edit_text(cb.message, texts.render_top_reset_done(texts.user_nick(cb.from_user)))


@router.message(Command("status"), F.chat.type == "private")
async def cmd_status(message: Message, engine: DuelEngine, repo: BaseRepository, config: Config, started_at: float):
    if not config.admin_id or message.from_user.id != config.admin_id:
        return
    days, hours, minutes = system_metrics.get_uptime(started_at)
    db_stats = repo.get_health_stats()
    ram_mb = system_metrics.get_process_ram_mb()
    disk_kb = system_metrics.get_disk_usage_kb(config.db_path, config.log_file)
    log_errors = system_metrics.summarize_log_errors(config.log_file)
    last_error = html.escape(log_errors.last_error) if log_errors.last_error else "Ошибок нет ✅"
    text = (
        f"🟢 <b>СТАТУС БОТА: ОК</b>\n\n"
        f"⏱ <b>Аптайм:</b> {days}д {hours}ч {minutes}м\n"
        f"🧠 <b>Память процесса (ОЗУ):</b> {ram_mb} МБ\n"
        f"💾 <b>Место на диске (БД + логи):</b> {disk_kb} КБ\n"
        f"⚔️ <b>Активных дуэлей сейчас:</b> {len(engine.sessions)}\n"
        f"📥 <b>Очередь записи в БД:</b> {db_stats.get('queue_size', 0)}\n\n"
        f"🗄 <b>База данных ({db_stats['db_size_kb']} КБ):</b>\n"
        f"• Всего дуэлей сыграно: <b>{db_stats['total_duels']}</b>\n"
        f"• Дуэлей за сегодня (UTC): <b>{db_stats['today_duels']}</b>\n"
        f"• Игроков в кэше ников: <b>{db_stats['cached_users']}</b>\n\n"
        f"📜 <b>Логи ({config.log_file}):</b>\n"
        f"• Записей ERROR: <b>{log_errors.count}</b>\n"
        f"• Последняя ошибка:\n<code>{last_error}</code>"
    )
    await safe_answer(message, text)
