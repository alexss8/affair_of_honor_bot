"""Корневой роутер бота: подключает middleware и все тематические роутеры."""

from aiogram import Dispatcher, Router

from bot.config import Config
from bot.telegram.handlers import admin, battle, common, debug, errors, fallback, invite, lifecycle
from bot.telegram.middlewares import cache_user_middleware, log_context_middleware

router = Router(name="root")
for observer in (router.message, router.callback_query, router.my_chat_member):
    observer.outer_middleware(log_context_middleware)
router.message.outer_middleware(cache_user_middleware)
router.callback_query.outer_middleware(cache_user_middleware)
# fallback — строго последним: ловит нажатия, которые не подошли ни одному роутеру выше.
router.include_routers(
    errors.router,
    lifecycle.router,
    common.router,
    admin.router,
    invite.router,
    battle.router,
    fallback.router,
)


def include_handlers(dp: Dispatcher, config: Config) -> None:
    """Подключает обработчики; отладочный стенд — только при DEBUG_TOOLS и раньше корневого роутера."""
    if config.debug_tools:
        dp.include_router(debug.router)
    dp.include_router(router)
