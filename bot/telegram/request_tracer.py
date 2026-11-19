"""Трассировка запросов к Telegram для отладочного стенда (DEBUG_TOOLS=1): строка в лог на каждый запрос.

Стоит в сессии бота снаружи лимитера, поэтому время запроса включает ожидание слота. По этим строкам видно,
какие запросы ушли, сколько ждали, какой текст карточки дошёл до Telegram и чем ответил Telegram.
"""

import logging
import time
from collections.abc import Callable

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType

from bot.logger import log_chat
from bot.telegram.rate_limiter import RateLimiter

logger = logging.getLogger("duel_bot")

TEXT_PREVIEW_LEN = 80


def _text_preview(method: TelegramMethod) -> str:
    text = getattr(method, "text", None)
    if not isinstance(text, str):
        return ""
    line = " ".join(text.split())
    return f" «{line[:TEXT_PREVIEW_LEN]}{'…' if len(line) > TEXT_PREVIEW_LEN else ''}»"


class RequestTracer(BaseRequestMiddleware):
    def __init__(self, limiter: RateLimiter, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._limiter = limiter
        self._clock = clock

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        chat_id = getattr(method, "chat_id", None)
        api_method = method.__api_method__
        queue = self._limiter.estimate_wait(chat_id) if chat_id is not None else 0.0
        started = self._clock()
        outcome = "ок"
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as e:
            outcome = f"429, retry_after={e.retry_after}"
            raise
        except TelegramAPIError as e:
            outcome = f"{type(e).__name__}: {e.message}"
            raise
        except Exception as e:
            outcome = type(e).__name__
            raise
        finally:
            with log_chat(chat_id if isinstance(chat_id, int) else None):
                logger.info(
                    "Запрос %s: %s за %.2f с (очередь ~%.1f с)%s",
                    api_method,
                    outcome,
                    self._clock() - started,
                    queue,
                    _text_preview(method),
                )
