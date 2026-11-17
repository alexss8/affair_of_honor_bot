import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats

from bot.config import EXIT_CONFIG_ERROR, Config, ConfigError, load_config
from bot.domain.engine import DuelEngine
from bot.logger import setup_logger
from bot.services.duel_service import DuelService
from bot.storage.queued_repo import QueuedRepository
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.handlers import router


async def main(config: Config) -> None:
    logger = setup_logger(config.log_file)

    sqlite_repo = SQLiteRepository(config.db_path)
    repo = QueuedRepository(target_repo=sqlite_repo, logger=logger)
    schema_version = repo.init()
    repo.start_worker()

    engine = DuelEngine(logger=logger, default_hp=config.default_hp, invite_ttl_sec=config.invite_ttl_sec)
    service = DuelService(repo=repo, logger=logger, top_limit=config.top_limit)

    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["service"] = service
    dp["logger"] = logger
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
            f"Бот запущен! Настройки: {config.default_hp} HP, топ-{config.top_limit}. Схема БД: v{schema_version}."
        )
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота, дописываем фоновую очередь...")
        await repo.stop_worker()
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
