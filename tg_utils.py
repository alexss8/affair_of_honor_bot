import asyncio
import logging
from typing import Optional
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger("duel_bot")

async def safe_answer_callback(
    cb: CallbackQuery, text: Optional[str] = None, show_alert: bool = False
) -> None:
    try:
        await cb.answer(text=text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" in e.message or "query ID is invalid" in e.message:
            return
        logger.warning(f"Ошибка cb.answer: {e.message}")
    except (TelegramNetworkError, TelegramServerError):
        pass

async def safe_edit_text(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    for _ in range(2):
        try:
            await message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
            return True
        except TelegramRetryAfter as e:
            logger.warning(f"Лимит Telegram (429). Ждем {e.retry_after} сек...")
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            msg = e.message.lower()
            if "message is not modified" in msg:
                return True
            if any(err in msg for err in ("message to edit not found", "message can't be edited", "message_id_invalid")):
                logger.warning(f"Сообщение дуэли удалено или недоступно в чате {message.chat.id}")
                return False
            logger.error(f"Ошибка BadRequest при редактировании: {e.message}")
            return True
        except (TelegramNotFound, TelegramForbiddenError):
            return False
        except (TelegramNetworkError, TelegramServerError) as e:
            logger.warning(f"Сетевой сбой Telegram при редактировании: {e}")
            await asyncio.sleep(1)
    return True

async def safe_edit_markup(
    message: Message, reply_markup: Optional[InlineKeyboardMarkup] = None
) -> bool:
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        msg = e.message.lower()
        if "message is not modified" in msg:
            return True
        if any(err in msg for err in ("message to edit not found", "message can't be edited")):
            return False
        logger.warning(f"Ошибка BadRequest при обновлении кнопок: {e.message}")
        return True
    except (TelegramNotFound, TelegramForbiddenError):
        return False
    except (TelegramNetworkError, TelegramServerError):
        return True

async def safe_reply(message: Message, text: str) -> None:
    try:
        await message.reply(text, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message to be replied not found" in e.message.lower():
            await message.answer(text, parse_mode="HTML")
        else:
            logger.warning(f"Ошибка при ответе на сообщение: {e.message}")
    except (TelegramForbiddenError, TelegramNetworkError, TelegramServerError):
        pass
