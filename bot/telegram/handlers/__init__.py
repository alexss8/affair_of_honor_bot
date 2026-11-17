"""Корневой роутер бота: подключает middleware и все тематические роутеры."""

from aiogram import Router

from bot.telegram.handlers import admin, battle, common, errors, fallback, invite, lifecycle
from bot.telegram.middlewares import cache_user_middleware

router = Router(name="root")
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
