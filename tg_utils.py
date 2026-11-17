import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message

logger = logging.getLogger("duel_bot")

_EDIT_GONE_ERRORS = ("message to edit not found", "message can't be edited", "message_id_invalid")


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


async def _edit_with_retry(edit: Callable[[], Awaitable[Any]], chat_id: int) -> bool:
    """Выполняет правку сообщения с одним повтором при 429/сетевом сбое.

    Returns:
        False, если сообщение удалено или недоступно (сессию нужно сбросить), иначе True.
    """
    for _ in range(2):
        try:
            await edit()
            return True
        except TelegramRetryAfter as e:
            logger.warning(f"Лимит Telegram (429). Ждем {e.retry_after} сек...")
            await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            msg = e.message.lower()
            if "message is not modified" in msg:
                return True
            if any(err in msg for err in _EDIT_GONE_ERRORS):
                logger.warning(f"Сообщение дуэли удалено или недоступно в чате {chat_id}")
                return False
            logger.error(f"Ошибка BadRequest при редактировании: {e.message}")
            return True
        except (TelegramNotFound, TelegramForbiddenError):
            return False
        except (TelegramNetworkError, TelegramServerError) as e:
            logger.warning(f"Сетевой сбой Telegram при редактировании: {e}")
            await asyncio.sleep(1)
    return True


async def safe_edit_text(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    return await _edit_with_retry(
        lambda: message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML"),
        message.chat.id,
    )


async def safe_edit_text_by_id(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    """То же, что safe_edit_text, когда объекта Message нет (известны только chat_id и message_id)."""
    return await _edit_with_retry(
        lambda: bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode="HTML"
        ),
        chat_id,
    )


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
        if any(err in msg for err in _EDIT_GONE_ERRORS):
            return False
        logger.warning(f"Ошибка BadRequest при обновлении кнопок: {e.message}")
        return True
    except (TelegramNotFound, TelegramForbiddenError):
        return False
    except (TelegramNetworkError, TelegramServerError):
        return True


async def safe_answer(
    target: Any,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Message]:
    """Отправляет сообщение в чат события (Message, ChatMemberUpdated и т.п. с методом .answer).

    Один повтор при 429. Возвращает отправленное сообщение или None, если отправить не удалось.
    """
    kwargs: dict[str, Any] = {"parse_mode": "HTML"}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    for attempt in range(2):
        try:
            return await target.answer(text, **kwargs)
        except TelegramRetryAfter as e:
            if attempt == 0:
                logger.warning(f"Лимит Telegram (429) при отправке. Ждем {e.retry_after} сек...")
                await asyncio.sleep(e.retry_after)
        except TelegramBadRequest as e:
            logger.warning(f"Ошибка при отправке сообщения: {e.message}")
            return None
        except (TelegramForbiddenError, TelegramNetworkError, TelegramServerError) as e:
            logger.warning(f"Не удалось отправить сообщение: {e}")
            return None
    return None


async def safe_get_chat(bot: Bot, chat_id: int) -> Optional[Chat]:
    """Возвращает Chat или None, если Telegram его не отдал (например, "chat not found")."""
    try:
        return await bot.get_chat(chat_id)
    except (TelegramBadRequest, TelegramNotFound, TelegramForbiddenError,
            TelegramNetworkError, TelegramServerError, TelegramRetryAfter) as e:
        logger.warning(f"Не удалось получить чат {chat_id}: {e}")
        return None


async def safe_reply(message: Message, text: str) -> None:
    try:
        await message.reply(text, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message to be replied not found" in e.message.lower():
            await safe_answer(message, text)
        else:
            logger.warning(f"Ошибка при ответе на сообщение: {e.message}")
    except (TelegramForbiddenError, TelegramNetworkError, TelegramServerError):
        pass
