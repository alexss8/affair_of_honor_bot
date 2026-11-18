import html
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramServerError
from aiogram.types import ErrorEvent

from bot.config import Config
from bot.services.alert_throttle import AlertThrottle, error_key
from bot.telegram.ui.texts import plural

router = Router(name="errors")


@router.error()
async def global_error_handler(
    event: ErrorEvent, logger: logging.Logger, bot: Bot, config: Config, alerts: AlertThrottle
):
    exc = event.exception
    if isinstance(exc, (TelegramNetworkError, TelegramServerError, TelegramForbiddenError)):
        logger.warning(f"Сетевая ошибка или ограничение доступа Telegram: {exc}")
        return
    logger.error(f"Непредвиденная ошибка при обработке события: {exc}", exc_info=True)
    if not config.admin_id:
        return
    repeats = alerts.register(error_key(exc))
    if repeats is None:
        return
    text = f"🚨 <b>Ошибка в боте:</b>\n<code>{html.escape(str(exc)[:500])}</code>"
    if repeats:
        text += f"\nС прошлого алерта повторялась ещё {repeats} {plural(repeats, 'раз', 'раза', 'раз')}."
    try:
        await bot.send_message(config.admin_id, text, parse_mode="HTML")
    except Exception:
        pass
