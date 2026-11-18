import asyncio
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats

from bot.config import EXIT_CONFIG_ERROR, Config, ConfigError, load_config
from bot.domain.engine import DuelEngine
from bot.logger import setup_logger
from bot.services.alert_throttle import AlertThrottle
from bot.services.duel_service import DuelService
from bot.storage.queued_repo import QueuedRepository
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.handlers import router
from bot.telegram.rate_limiter import RateLimiter


def create_bot(token: str, limiter: RateLimiter) -> Bot:
    """Бот, все запросы которого идут через лимитер."""
    bot = Bot(token=token)
    bot.session.middleware(limiter)
    return bot


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
    bot = create_bot(config.bot_token, limiter)
    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["service"] = service
    dp["logger"] = logger
    dp["alerts"] = AlertThrottle()
    dp["started_at"] = started_at
    dp["limiter"] = limiter
    dp.include_router(router)

    try:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="duel", description="Вызвать на дуэль (@ник или Reply)"),
                BotCommand(command="cancel", description="Отменить свою зависшую дуэль"),
                BotCommand(command="top", description=f"Топ-{config.top_limit} дуэлянтов чата"),
                BotCommand(command="help", description="Правила игры и команды"),
            ],
            scope=BotCommandScopeAllGroupChats(),
        )

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
