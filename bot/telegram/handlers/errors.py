import html
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramServerError
from aiogram.types import ErrorEvent

from bot.config import Config

router = Router(name="errors")


@router.error()
async def global_error_handler(event: ErrorEvent, logger: logging.Logger, bot: Bot, config: Config):
    exc = event.exception
    if isinstance(exc, (TelegramNetworkError, TelegramServerError, TelegramForbiddenError)):
        logger.warning(f"Сетевая ошибка или ограничение доступа Telegram: {exc}")
        return
    logger.error(f"Непредвиденная ошибка при обработке события: {exc}", exc_info=True)
    if config.admin_id:
        try:
            await bot.send_message(
                config.admin_id,
                f"🚨 <b>Ошибка в боте:</b>\n<code>{html.escape(str(exc)[:500])}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
