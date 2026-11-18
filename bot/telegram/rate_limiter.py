"""Лимитер запросов к Telegram: отправки, правки и удаления сообщений ждут свободного слота, а не ловят 429.

Подключается к сессии бота (`bot.session.middleware(RateLimiter())`), поэтому через него идут все запросы —
и из обработчиков, и рассылки при старте и остановке. Лимиты — из FAQ Telegram (https://core.telegram.org/bots/faq),
у группы стартовый запас вдвое; правило везде одно: не больше N запросов за любые T секунд.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType

GLOBAL_LIMIT, GLOBAL_PERIOD_SEC = 25, 1.0
# В среднем 1 запрос в секунду, короткий всплеск до 3 подряд (FAQ допускает короткие всплески)
CHAT_LIMIT, CHAT_PERIOD_SEC = 3, 3.0
GROUP_LIMIT, GROUP_PERIOD_SEC = 40, 60.0

# Ограничиваются только запросы, которые создают, меняют или удаляют сообщения в чате
_LIMITED_PREFIXES = ("send", "edit", "delete", "copy", "forward")


class _Window:
    """Время недавних запросов за последние period секунд."""

    def __init__(self, period: float) -> None:
        self.period = period
        self._sent: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._sent and self._sent[0] <= now - self.period:
            self._sent.popleft()

    def wait_time(self, now: float, limit: int) -> float:
        """Сколько ждать, чтобы запрос не стал (limit + 1)-м за period секунд."""
        self._prune(now)
        if len(self._sent) < limit:
            return 0.0
        return self._sent[-limit] + self.period - now

    def record(self, now: float) -> None:
        self._sent.append(now)

    def is_empty(self, now: float) -> bool:
        self._prune(now)
        return not self._sent


@dataclass
class _ChatState:
    is_group: bool
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # очередь запросов чата по порядку
    chat: _Window = field(default_factory=lambda: _Window(CHAT_PERIOD_SEC))
    group: _Window = field(default_factory=lambda: _Window(GROUP_PERIOD_SEC))


def _is_group(chat_id: int | str) -> bool:
    # Отрицательный id — группа или канал; строковый @username считаем группой — так осторожнее
    return isinstance(chat_id, str) or chat_id < 0


class RateLimiter(BaseRequestMiddleware):
    """Middleware сессии aiogram: держит лимиты на весь бот, на чат и на группу."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.group_limit = GROUP_LIMIT
        self._clock = clock
        self._sleep = sleep
        self._global = _Window(GLOBAL_PERIOD_SEC)
        self._chats: dict[int | str, _ChatState] = {}
        self._last_sweep = clock()

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        chat_id = getattr(method, "chat_id", None)
        if chat_id is not None and method.__api_method__.startswith(_LIMITED_PREFIXES):
            state = self._chat_state(chat_id)
            async with state.lock:
                await self._wait_slot(state)
        return await make_request(bot, method)

    def _chat_state(self, chat_id: int | str) -> _ChatState:
        now = self._clock()
        if now - self._last_sweep >= GROUP_PERIOD_SEC:
            self._drop_idle_chats(now)
        state = self._chats.get(chat_id)
        if state is None:
            state = self._chats[chat_id] = _ChatState(is_group=_is_group(chat_id))
        return state

    def _drop_idle_chats(self, now: float) -> None:
        self._last_sweep = now
        idle = [
            chat_id
            for chat_id, s in self._chats.items()
            if not s.lock.locked() and s.chat.is_empty(now) and s.group.is_empty(now)
        ]
        for chat_id in idle:
            del self._chats[chat_id]

    async def _wait_slot(self, state: _ChatState) -> None:
        while True:
            # Лимит группы читается на каждой проверке: его снижение действует и на уже ждущие запросы
            windows = [(self._global, GLOBAL_LIMIT), (state.chat, CHAT_LIMIT)]
            if state.is_group:
                windows.append((state.group, self.group_limit))
            now = self._clock()
            delay = max(window.wait_time(now, limit) for window, limit in windows)
            if delay <= 0:
                for window, _ in windows:
                    window.record(now)
                return
            await self._sleep(delay)
