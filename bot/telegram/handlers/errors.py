import html
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramServerError
from aiogram.types import ErrorEvent, Update

from bot.config import Config
from bot.logger import log_chat
from bot.services.alert_throttle import AlertThrottle, error_key
from bot.telegram.middlewares import event_chat
from bot.telegram.ui.texts import plural

router = Router(name="errors")


def _update_chat_id(update: Update) -> int | None:
    try:
        chat = event_chat(update.event)
    except LookupError:  # aiogram's UpdateTypeLookupError: update without a known event
        return None
    return chat.id if chat else None


@router.error()
async def global_error_handler(
    event: ErrorEvent, logger: logging.Logger, bot: Bot, config: Config, alerts: AlertThrottle
):
    exc = event.exception
    # aiogram calls error handlers outside the per-message middleware, so the chat is set here again.
    with log_chat(_update_chat_id(event.update)):
        if isinstance(exc, (TelegramNetworkError, TelegramServerError, TelegramForbiddenError)):
            logger.warning("Сетевая ошибка или ограничение доступа Telegram: %s", exc)
            return
        logger.error("Непредвиденная ошибка при обработке события: %s", exc, exc_info=True)
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
