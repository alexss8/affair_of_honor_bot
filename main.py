import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats

from config import load_config
from engine import DuelEngine
from handlers import router
from logger import setup_logger
from queued_repo import QueuedRepository
from repository import SQLiteRepository

async def main() -> None:
    config = load_config()
    logger = setup_logger(config.log_file)

    sqlite_repo = SQLiteRepository(config.db_path)
    repo = QueuedRepository(target_repo=sqlite_repo, logger=logger)
    repo.init()
    repo.start_worker()

    engine = DuelEngine(repo=repo, logger=logger, default_hp=config.default_hp)

    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
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
            scope=BotCommandScopeAllGroupChats()
        )

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info(f"Бот запущен! Настройки: {config.default_hp} HP, топ-{config.top_limit}.")
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка бота, дописываем фоновую очередь...")
        await repo.stop_worker()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
