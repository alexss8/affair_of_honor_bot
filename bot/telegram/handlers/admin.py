import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.services import system_metrics
from bot.storage.base import BaseRepository
from bot.telegram.safe_api import safe_answer

router = Router(name="admin")


@router.message(Command("status"), F.chat.type == "private")
async def cmd_status(message: Message, engine: DuelEngine, repo: BaseRepository, config: Config):
    if not config.admin_id or message.from_user.id != config.admin_id:
        return
    days, hours, minutes = system_metrics.get_uptime()
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
