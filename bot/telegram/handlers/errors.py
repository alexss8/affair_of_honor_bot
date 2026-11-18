import html
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import ErrorEvent, Update

from bot.config import Config
from bot.logger import log_chat
from bot.services.alert_throttle import AlertThrottle, error_key
from bot.telegram.middlewares import event_chat
from bot.telegram.safe_api import safe_answer_callback, safe_reply
from bot.telegram.ui.texts import ERROR_NOTICE, plural

router = Router(name="errors")

ERROR_NOTICE_COOLDOWN_SEC = 300  # сообщение о сбое в чат — не чаще раза в 5 минут на чат


def _update_chat_id(update: Update) -> int | None:
    try:
        chat = event_chat(update.event)
    except LookupError:  # aiogram's UpdateTypeLookupError: update without a known event
        return None
    return chat.id if chat else None


async def _notify_player(update: Update, exc: Exception, error_notices: AlertThrottle) -> None:
    """Сообщает игроку о сбое: на нажатие кнопки — окном, на упавшую команду — в чат. Текст исключения не показываем."""
    if isinstance(exc, TelegramRetryAfter):
        return  # ответ сам потратил бы лимит
    if update.callback_query:
        await safe_answer_callback(update.callback_query, ERROR_NOTICE, show_alert=True)
    elif update.message and (update.message.text or "").startswith("/"):
        if error_notices.register(update.message.chat.id) is not None:
            await safe_reply(update.message, ERROR_NOTICE)


@router.error()
async def global_error_handler(
    event: ErrorEvent,
    logger: logging.Logger,
    bot: Bot,
    config: Config,
    alerts: AlertThrottle,
    error_notices: AlertThrottle,
):
    exc = event.exception
    # aiogram calls error handlers outside the per-message middleware, so the chat is set here again.
    with log_chat(_update_chat_id(event.update)):
        if isinstance(exc, (TelegramNetworkError, TelegramServerError, TelegramForbiddenError)):
            logger.warning("Сетевая ошибка или ограничение доступа Telegram: %s", exc)
            return
        logger.error("Непредвиденная ошибка при обработке события: %s", exc, exc_info=True)
        await _notify_player(event.update, exc, error_notices)
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
