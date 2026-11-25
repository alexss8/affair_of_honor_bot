import asyncio
import logging
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeChat

from bot.config import EXIT_CONFIG_ERROR, Config, ConfigError, load_config
from bot.domain.engine import DuelEngine
from bot.logger import setup_logger
from bot.services.alert_throttle import AlertThrottle
from bot.services.duel_service import DuelService
from bot.storage.queued_repo import QueuedRepository
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.handlers import include_handlers
from bot.telegram.handlers.errors import ERROR_NOTICE_COOLDOWN_SEC
from bot.telegram.rate_limiter import RateLimiter
from bot.telegram.request_tracer import RequestTracer


def create_bot(token: str, limiter: RateLimiter, *, trace: bool = False) -> Bot:
    """Бот, все запросы которого идут через лимитер; trace — строка в лог на каждый запрос (отладочный стенд)."""
    bot = Bot(token=token)
    if trace:
        bot.session.middleware(RequestTracer(limiter))  # первым — снаружи лимитера, время включает ожидание
    bot.session.middleware(limiter)
    return bot


async def _set_owner_menu(bot: Bot, admin_id: int, logger: logging.Logger) -> None:
    """Меню служебных команд — только в личке владельца с ботом.

    Telegram отказывает, если владелец ещё не писал боту в личку; бот при этом должен стартовать.
    """
    commands = [
        BotCommand(command="admin", description="Служебные команды"),
        BotCommand(command="status", description="Состояние бота"),
    ]
    try:
        await bot.set_my_commands(commands=commands, scope=BotCommandScopeChat(chat_id=admin_id))
    except TelegramAPIError as e:
        logger.warning("Меню владельца не установлено (напишите боту в личку и перезапустите): %s", e)


async def main(config: Config) -> None:
    started_at = time.time()
    logger = setup_logger(config.log_file)

    sqlite_repo = SQLiteRepository(config.db_path)
    repo = QueuedRepository(target_repo=sqlite_repo, logger=logger)
    schema_version = repo.init()
    repo.start_worker()

    engine = DuelEngine(logger=logger, default_hp=config.default_hp, invite_ttl_sec=config.invite_ttl_sec)
    service = DuelService(repo=repo, logger=logger, top_limit=config.top_limit)

    limiter = RateLimiter()
    bot = create_bot(config.bot_token, limiter, trace=config.debug_tools)
    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["service"] = service
    dp["logger"] = logger
    dp["alerts"] = AlertThrottle()
    dp["error_notices"] = AlertThrottle(cooldown_sec=ERROR_NOTICE_COOLDOWN_SEC)
    dp["started_at"] = started_at
    dp["limiter"] = limiter
    include_handlers(dp, config)

    try:
        group_commands = [
            BotCommand(command="duel", description="Вызвать на дуэль (@ник или Reply)"),
            BotCommand(command="cancel", description="Отменить свою зависшую дуэль"),
            BotCommand(command="top", description=f"Топ-{config.top_limit} дуэлянтов чата"),
            BotCommand(command="ttop", description="Таблица текущего турнира"),
            BotCommand(command="help", description="Правила игры и команды"),
        ]
        await bot.set_my_commands(commands=group_commands, scope=BotCommandScopeAllGroupChats())
        if config.admin_id:
            await _set_owner_menu(bot, config.admin_id, logger)

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info(
            "Бот запущен! Настройки: %s HP, топ-%s. Схема БД: v%s.", config.default_hp, config.top_limit, schema_version
        )
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота, дописываем фоновую очередь...")
        await repo.stop_worker()
        repo.close()
        await bot.session.close()


def run() -> None:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"❌ {e}\nИсправьте .env (образец — .env.example) и запустите бота снова.", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    asyncio.run(main(config))


if __name__ == "__main__":
    run()
